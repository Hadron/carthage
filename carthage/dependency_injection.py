# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import contextvars, enum, inspect, typing, weakref
import collections.abc
import asyncio, functools
import logging
import types
import sys
from dataclasses import dataclass

from . import tb_utils, event

_chatty_modules = {asyncio.futures, asyncio.tasks, sys.modules[__name__]}
logger = logging.getLogger('carthage.dependency_injection')
logger.setLevel('INFO')

class ReadyState(enum.Enum):
    NOT_READY = 0
    RESOLVED = 1
    READY_PENDING = 2
    READY = 3

instantiate_to_ready = contextvars.ContextVar('instantiate_to_ready', default = True)

# While this is needed by InjectableModelType's add_provider, it is
# not part of the public api
def default_injection_key(p):
    if isinstance(p, DependencyProvider): raise NotImplementedError
    if (isinstance(p, type) and issubclass(p, Injectable)):
        k = p.default_class_injection_key()
    elif isinstance(p, Injectable):
        k = p.default_instance_injection_key()
    elif isinstance(p, type):
        k = InjectionKey(p)
    else:
        # not a type and not an Injectable
        k = InjectionKey(p.__class__)
    return k

class Injectable:

    '''Represents a class that has dependencies injected into it. By default, the :meth:`__init__` will:

    * Store any keyword arguments corresponding to injection dependencies into instance variables of the same name as the keyword argument

    * Remove these keyword arguments prior to calling the superclass init.

    So for example::

        @inject(router = SiteRouter)
        class Receiver(Injectable): pass

    When ``Receiver`` is instantiated, its instances will have the *router* attribute set.

    It is **recommended** but not required that classes with injected
    dependencies inherit from *Injectable*.  The
    :meth:`satisfies_injection_key` and
    :meth:`supplementary_injection_keys` protocols are only available
    to classes that do inherit from *Injectable*.

    Subclasses that may be mixins and that wish injected dependency handling different than the keyword assignment provided by *Injectable* must inherit from *Injectable*.

    This class does not have :class:`Injector` as an injected dependency.  It is possible to have injected dependencies without doing so.  However, in a dependency is *Injector*, then that injector will be :meth:`claimed <Injector.claim>`.

    '''

    def __init__(self, *args, **kwargs):
        autokwargs =set(getattr(self, '_injection_autokwargs', set()))
        for k, d in getattr(self, '_injection_dependencies', {}).items():
            if k in kwargs:
                if d is _injector_injection_key:
                    injector = kwargs.pop(k)
                    setattr(self, k, injector.claim(self))
                else:
                    setattr(self, k, kwargs.pop(k))
                try: autokwargs.remove(k)
                except KeyError: pass

        if autokwargs:
            raise TypeError(f'The following dependencies were not specified: {autokwargs}')

        try:
            super().__init__(*args, **kwargs)
        except TypeError as t:
            if 'object.__init__()' in str(t):
                raise TypeError(f'The following extra arguments were specified: {list(kwargs.keys())}')
            raise

    def close(self, canceled_futures = None):
        if hasattr(self, 'injector'):
            self.injector.close(canceled_futures)
        
    @classmethod
    def supplementary_injection_keys(cls, k: InjectionKey):
        '''
        Returns  an iteration of :class:`InjectionKeys <InjectionKey>` that should be added  to an injector when this class is added.  The current injection key is taken as an argument so that constraints applied can modify what keys are added.


        '''
        for c in cls.__mro__:
            if c in (Injectable, AsyncInjectable): continue
            if issubclass(c,Injectable) and c != k.target:
                yield InjectionKey(c)
                if k.constraints: yield InjectionKey(c, **k.constraints)
            elif c is k.target and k.constraints:
                    yield InjectionKey(c, **k.constraints)

    @classmethod
    def satisfies_injection_key(cls, k):
        if k == InjectionKey(cls): return True
        if isinstance(k.target, (str, tuple)): return True
        return k in cls.supplementary_injection_keys(k)

    @classmethod
    def default_class_injection_key(cls):
        '''Called by :meth:`Injector.add_provider()` in the single argument form to get the injection key to use when this class is added to provide a dependency.'''
        return InjectionKey(cls)

    def default_instance_injection_key(self):
        "Called when an instance of an injectable is used to add a dependency provider bby the single argument form of :meth:`Injectable.add_provider()`"
        return InjectionKey(self.__class__)
    
    
class DependencyProvider:
    __slots__ = ('provider',
                 'allow_multiple',
                 'close',
                 )

    def __init__(self, provider, allow_multiple = False, close = True):
        self.provider = provider
        self.allow_multiple = allow_multiple
        self.close = close

    def __repr__(self):
        return "<DependencyProvider allow_multiple={}: {}>".format(
self.allow_multiple, repr(self.provider))

    @property
    def is_factory(self):
        return (isinstance(self.provider, type) and issubclass(self.provider, Injectable)) \
            or asyncio.iscoroutinefunction(self.provider) \
            or asyncio.isfuture(self.provider) \
            or directly_has_dependencies(self.provider)

    def record_instantiation(self, instance, k, satisfy_against, final):
        dp = satisfy_against._providers.setdefault(k, DependencyProvider(instance, self.allow_multiple, close = self.close))
        assert dp.is_factory or dp.provider is instance
        dp.provider = instance
        if final:
            #If requested again, would try to instantiate again.
            if dp.is_factory:
                dp.provider = dependency_quote(dp.provider)
        return dp



class InjectionFailed(RuntimeError):

    def __init__(self, k):
        super().__init__(f"Error resolving dependency for {k}")
        self.failed_dependency = k

class ExistingProvider(RuntimeError):

    def __init__(self, k):
        super().__init__(f'Provider for {k} already registered')
        self.existing_key = k

class  InjectorClosed(RuntimeError): pass

class AsyncRequired(RuntimeError): pass

# Note that after @inject is defined, this class is redecorated to take parent_injector as a dependency so that
#    injector = sub_injector(Injector)
# works
class Injector(Injectable, event.EventListener):

    def __init__(self, *providers,
                 parent_injector = None):
        self._providers = {}
        self._pending = weakref.WeakSet()
        if parent_injector is None and len(providers) > 0:
            if isinstance(providers[0], Injector):
                parent_injector = providers[0]
                providers = providers[1:]

        self.parent_injector = parent_injector
        self.claimed_by = None
        if self.parent_injector:
            event_scope = self.parent_injector._event_scope
            event_scope.add_child(parent_injector, self)
        else: event_scope = None
        super().__init__(event_scope = event_scope)
        for p in providers:
            self.add_provider(p)
        self.add_provider(self) #Make sure we can inject an Injector
        self.add_provider(InjectionKey(AsyncInjector ), AsyncInjector, allow_multiple = True)
        self.closed = False
        self._closing = False


    def claim(self, claimed_by = True):
        '''
        Take ownership of the injector.

        :param claimed_by: Either *True* or an object that this injector is marked as belonging to.

        Returns either *self* or a new subinjector.

        '''
        if self.claimed_by:
            return self(type(self)).claim(claimed_by)
        else:
            if claimed_by is True or isinstance(claimed_by, str):
                self.claimed_by = claimed_by
            else: self.claimed_by = weakref.ref(claimed_by)
            return self


    def add_provider(self, k, p = None, *,
                     allow_multiple = False,
                     close = True,
                     replace = False):
        '''Add a provider for a dependency

        Either called as ``add_provider(provider)`` or
        ``add_provider(injection_key, provider)``\ .  In the first form, a key is
        automatically constructed.  For :class:`Injectable` objects, see :meth:`Injectable.default_instance_injection_key()`.  For *Injectable* types, see :meth:`Injectable.default_class_injection_key()`.  For other objects, the unconstrained injection key for types (or for the type of the object) is used.

        :param allow_multiple: If true, then this provider may be instantiated multiple times in sub-injectors.  If false (the default) then the provider will be instantiated on the injector where it is added and used by all sub-injectors.

        :param close: If true (the default), then closing the injector will close or cancel this provider.  If false, then the provider will not be deallocated.  As an example, if the :class:`asyncio.AbstractEventLoop` is added as a provider, but closing this injector should not close the loop and end all async operations, then close can be set to false.

        :param replace: If True, an existing provider is being updated.  :meth:`replace_provider` is a convenience function for calling :meth:`add_provider` with *replace* set to True.  Replacing providers may lead to inconsistent results if the provider has already been injected to fill a dependency in a constructed object.

'''
        if p is None and not isinstance(k, InjectionKey):
            p,k = k,p #swap; we construct the key later

        if k is None:
            k = default_injection_key(p)
        if not isinstance(p, DependencyProvider):
            p = DependencyProvider(p, allow_multiple = allow_multiple, close = close)
        assert isinstance(k,InjectionKey)
        if k in self:
            if p is self._get(k): return k
            existing_provider = self._get(k)
            if replace:
                existing_provider.provider = p.provider
            else: raise ExistingProvider(k)
        else:
            self._providers[k] = p
        for k2 in k.supplementary_injection_keys(p.provider):
            if k2 not in self:
                self._providers[k2] = p
        return k

    def replace_provider(self, *args, **kwargs):
        return self.add_provider( *args, **kwargs, replace = True)

    def _get(self, k):
        return self._providers[k]

    def _get_parent(self, k):
        #Returns  DependencyProvider, instantiation_target
        injector = self
        while injector is not None:
            try:
                # If the key allows multiple providers, then
                # satisfy against ourself and store the result in
                # ourself.  Otherwise if a single provider is
                # required, then satisfy against the injector
                # where the key is introduced and store there.
                p = injector._providers[k]
                return p, (self if p.allow_multiple else injector)
            except KeyError:
                injector = injector.parent_injector
        raise KeyError("{} not found".format(k))

    def injector_containing(self, k):
        '''
Return the first injector in our parent chain containing *k* or None if there is no such injector.

        If *k* has not yet been instantiated, this injector would be the one against which the instantiation is recorded unless the provider was added with the *allow_multiple* argument to :meth:`add_provider()`.
        '''
        if not isinstance(k, InjectionKey):
            k = InjectionKey(k)
        injector = self
        while injector and not k in injector:
            injector = injector.parent_injector
        if injector and (k in injector): return injector
        return None

    def __contains__(self, k):
        if not isinstance(k, InjectionKey):
            k = InjectionKey(k)
        return k in self._providers

    def _check_closed(self):
        if self.closed:
            raise InjectorClosed("Injector is closed")

    def filter(self,
               target: type,
               predicate: typing.Union[list, typing.Callable] = None,
               stop_at: Injector = None):
        '''
        :return: Set of :class:`InjectionKey` with target type of
            *target* and satisfying *predicate* in the current injector
            and its parents.

        :param predicate: A list of constraints that must all be present in the key, or a callable that returns true if the key should be included.

        :param target: A target type to filter against.

        :param stop_at: An injector which must be a parent of this injector.  Do not progress past that injector in finding keys.  So if *stop_at* is *self*, only locally registered keys are returned.


        Example usage would be to find all registered plugins similar to the following::

            plugin_keys =injector.filter(CarthagePlugin, ['name'])

        '''
        def filter_for_constraints(k):
            return all(map(lambda c: c in k.constraints, constraints))
        if isinstance(predicate, list):
            constraints = predicate
            predicate = filter_for_constraints
        result = set(filter(lambda k: k.target is target and predicate(k), self._providers.keys()))
        if stop_at and not self.parent_injector:
            raise ValueError( f'{stop_at} was not in the parent chain')
        elif stop_at == self: pass# stop here
        elif self.parent_injector:
            result |= self.parent_injector.filter(target, predicate, stop_at = stop_at)
        return result

    def filter_instantiate(self, target, predicate, *, stop_at = None, ready = False):
        '''
        Like :meth:`filter` but an iterator returning tuples of keys instance.
'''
        for k in self.filter(target, predicate, stop_at = stop_at):
            if ready is not None: k = InjectionKey(k, _ready = ready)
            res = self.get_instance(k)
            if res is not None:
                yield k, res


    def __call__(self, cls, *args, **kwargs):

        '''Construct an instance of cls using the providers in this injector.
        Instantiate providers as needed.  In general a sub-injector is not
        constructed.  However if any keyword arguments pased in specify a
        dependency, then construct an injector for that.  Keyword arguments
        and arguments are passed to the class to construct the object.  If
        keyword arguments do specify a dep.dependency, they must satisfy the
        InjectionKey involved.
'''
        self._check_closed()
        return self._instantiate(
            cls, *args, **kwargs,
            _loop = None,
            _orig_k = None,
            _placement = None,
            _interim_placement = None)


    def get_instance(self, k,
                     placement = None,
                     loop = None, futures = None):
        '''
        Get an instance satisfying a given :class:`InjectionKey`.

        :param loop: An asyncio loop.  If provided, then asynchronous activities  can take place.
        :param placement: A function taking one argument.  Once the dependency is resolved, this function will be called with the result.  More convenient for asyncronous  operations.
        :param futures: If the result cannot be determined immediately, then a future will be added to this list.

        Note that If any of *loop* or *futures*,  are provided, both must be provided.  If *loop* is provided, then the return may be a future.

        '''
        if loop:
            assert futures is not None
        def do_place(res):
            provider.record_instantiation(res, k, satisfy_against, final = True)
            if placement: placement(res)
        def do_interim_place(res):
            provider.record_instantiation(res, k, satisfy_against, final = False)

        def future_resolved(fut):
            #our caller handles exceptions
            try:
                placement(fut.result())
            except BaseException: pass

        logger.debug("Looking up provider for {}".format(k))



        if not isinstance(k, InjectionKey):
            k = InjectionKey(k)
        try:
            provider, satisfy_against = self._get_parent(k)
        except KeyError:
            self._check_closed()
            if k.optional:
                if placement: placement(None)
                return None
            raise KeyError("No dependency for {}".format(k)) from None
        try:
            if k.ready is not None:
                ready_reset = instantiate_to_ready.set(k.ready)
            else: ready_reset = None
            to_ready = instantiate_to_ready.get()
            result = provider.provider
            if isinstance(result, dependency_quote):
                if placement: placement(result.value)
                return result.value
            elif isinstance(result, asyncio.Future):
                #If we have a future that already exists, we need to
                #arrange for placement to be called just as
                #_instantiate does for futures it generates.
                if placement: result.add_done_callback(future_resolved)
            elif provider.is_factory:
                result = satisfy_against._instantiate(
                    result,
                    _loop = loop,
                    _placement = do_place,
                    _orig_k = k,
                    _interim_placement = do_interim_place,
)
            if isinstance(result, asyncio.Future):
                futures.append(result)
                provider.record_instantiation(result, k, satisfy_against, final = False)
                return result
            elif to_ready and isinstance(result,AsyncInjectable) \
                     and result._async_ready_state != ReadyState.READY:
                    if not loop:
                        raise AsyncRequired(f"Requesting instantiation of {result} to ready, outside of async context")
                    if placement: placement(result)
                    future = loop.create_task(self._handle_async_injectable(result, resolv = False))
                    futures.append(future)
                    return future

        finally:
            if ready_reset is not None:
                instantiate_to_ready.reset(ready_reset)


        # Either not a future or not a factory
        if placement: placement(result)
        return result


    def _instantiate(self, cls, *args,
                     _loop,
                     _orig_k, _placement,
                     _interim_placement,
                     **kwargs):
        # _loop if  present means we can return something for which _is_async will return True
        # _orig_k affects error handling; the injection key we're resolving
        self._check_closed()
        def handle_result(done_future = None):
            # Called when all kwargs are populated
            try:
                try: res = cls(*args, **kwargs)
                except TypeError as e:
                    raise TypeError(f'Error constructing {cls}:') from e
                if self._is_async(res):
                    if not _loop:
                        raise AsyncRequired("Asynchronous dependency injected into non-asynchronous context")
                    if done_future is None: done_future = _loop.create_future()
                    self._handle_async(res, done_future,
                                       placement = _placement,
                                       interim_placement = _interim_placement,
                                       loop = _loop)
                    return done_future
                else:
                    if _placement: _placement(res)
                    if done_future: done_future.set_result(res)
                    return res
            except AsyncRequired: raise
            except Exception as e:
                tb_utils.filter_chatty_modules(e, _chatty_modules, 4)
                if _orig_k:
                    tb_utils.filter_before_here(e)
                    logger.exception(f'Error resolving dependency for {_orig_k}')
                    raise InjectionFailed(_orig_k) from e
                else:
                    raise

        def callback(fut):
            nonlocal done_future
            try:
                fut.result() #confirm all successful
                #If they were all successful, then kwargs is fully populated at this point
                handle_result(done_future)
            except asyncio.CancelledError:
                done_future.cancel()
            except Exception as e:
                done_future.set_exception(e)

        def kwarg_place(k):
            def collect(res):
                kwargs[k] = res
            return collect
        try:
            futures = []

            dks = set(filter(
                lambda k: cls._injection_dependencies[k] is not None,
                cls._injection_dependencies.keys()))
        except AttributeError: dks = set()
        injector = self # or sub_injector if created
        sub_injector = None
        kwarg_dependencies = set(kwargs.keys()) & dks
        try: #clean up sub_injector
            if kwarg_dependencies:
                sub_injector = (type(self))(self)
                injector = sub_injector
                for k in kwarg_dependencies:
                    provider = kwargs.pop(k)
                    dependency = cls._injection_dependencies[k]
                    if isinstance(provider,Injectable) and not provider.satisfies_injection_key(dependency):
                        raise UnsatisfactoryDependency(dependency, provider)
                    sub_injector.add_provider(dependency, provider, close = False)
            for k, d in (cls._injection_dependencies.items()) if dks else []:
                if d is None: continue
                injector.get_instance(d, placement = kwarg_place(k),
                                      loop = _loop, futures = futures)
            if futures:
                fut = asyncio.gather(*futures)
                fut.add_done_callback(callback)
                done_future = _loop.create_future()
                return done_future
            else:
                res = handle_result(done_future = None)
                return res
        finally:
            #Perhaps some day we need to clean up something about the sub_injector
            pass

    def _is_async(self, p):
        if isinstance(p, (collections.abc.Coroutine,
                          asyncio.Future)):
            return True
        elif isinstance(p, AsyncInjectable) and p._async_ready_state != ReadyState.READY:
            to_ready = instantiate_to_ready.get()
            return (p._async_ready_state ==  ReadyState.NOT_READY) or to_ready
        return False


    def _handle_async(self, p, done_future,
                      interim_placement, placement,
                      loop):
        def callback(f):
            try:
                res = f.result()
                done_future.set_result(res)
                if placement: placement(res)
            except asyncio.CancelledError:
                done_future.cancel()
            except BaseException as e:
                tb_utils.filter_chatty_modules(e, _chatty_modules, 3)
                done_future.set_exception(e)

        if not hasattr(self, 'loop'):
            self.loop = loop
        if isinstance(p, collections.abc.Coroutine):
            fut = asyncio.ensure_future(p, loop = loop)
        elif  isinstance(p, AsyncInjectable):
            fut =  asyncio.ensure_future(self._handle_async_injectable(p), loop = loop)
        elif isinstance(p, asyncio.Future):
            fut = p
        else:
                raise RuntimeError('_is_async returned True when _handle_async cannot handle')
        fut.add_done_callback(callback)
        self._pending.add(fut)

        if interim_placement: interim_placement(done_future)

    async def _handle_async_injectable(self, obj, resolv = True):
        try:
            #Don't bother running the resolve protocol for the base case
            if resolv and (obj._async_ready_state == ReadyState.NOT_READY):
                res = await obj.async_resolve()
                if res is None:
                    if obj._async_ready_state == ReadyState.NOT_READY:
                        obj._async_ready_state = ReadyState.RESOLVED
                    res = obj
                if self._is_async(res):
                    future = self.loop.create_future()
                    self._handle_async(res, done_future = future, placement = None,
                                       interim_placement = None, loop = self.loop)
                    return await future
                else: return res
            else: # no resolution required
                if instantiate_to_ready.get():
                    await obj.async_become_ready()
                    if not obj._async_ready_state == ReadyState.READY:
                        raise RuntimeError(f"async_ready for {obj.__class__.__name__} must chain back to AsyncInjectable.async_ready.")

                return obj
        except asyncio.CancelledError:
            if hasattr(obj, 'injector'):
                await shutdown_injector(obj.injector)
            raise

    def close(self, canceled_futures = None):
        '''
        Close all subinjectors or providers

        For every provider registered with this injector, call :meth:`close` if it is exists.  Then clear out all providers.  Note that this will also close sub-injectors of providers.

        If using `AsyncInjector`, it is better to call :func:`shutdown_injector` to cancel any running asynchronous tasks.

        If the provider's :meth:`close` method takes an argument called *canceled_futures* then the *canceled_futures* argument will be passed down.
        '''
        
        for f in self._pending:
            try: f.cancel()
            except: pass
        self._pending.clear()
        if self._closing: return
        self._closing = True
        providers = list(self._providers.values())
        for p in providers:
            if p.provider is self or not p.close: continue
            if hasattr(p.provider, 'close'):
                try:
                    _call_close(p.provider, canceled_futures)
                except Exception:
                    logger.exception("Error closing {}".format(p))
            elif asyncio.isfuture(p.provider):
                p.provider.cancel()
                if canceled_futures is not None: canceled_futures.append(p.provider)
        self.closed = True
        del providers
        self._providers.clear()
        self.parent_injector = None


    def __del__(self):
        if not self.closed:
            self.close()

    def __repr__(self):
        claim_str = ""
        if self.claimed_by is True:
            claim_str = f'claimed id: {id(self)}'
        elif isinstance(self.claimed_by, str):
            claim_str = self.claimed_by
        elif self.claimed_by is None:
            claim_str = f'unclaimed id: {id(self)}'
        elif self.claimed_by() is None:
            claim_str = "claimed by dead object"
        else: claim_str = f'claimed by {repr(self.claimed_by())}'
        return f'<{self.__class__.__name__} {claim_str}>'

    @property
    def is_claimed(self):
        return self.claimed_by is not None


_INJECTION_KEY_DEFAULTS = {
    'optional': False,
    'ready': None}

class InjectionKey:

    '''
    Represents information about what is requested to satisfy a dependency.

    :param target: A type or other object representing what is desired.

        * A type indicating an object of that type is desired

        * An object such as a string that is a unique identifier for what is desired

    :param _optional: If true, then if no provider for the dependency is registered, None will be passed rather than raising

    :param _ready:  If None (the default), then use the same readyness as the object into which this is being injected (or full readyness if this is a base operation).  If True, then to satisfy this dependency, the provided object must be fully ready.  If False, then a not ready object is preferred.

    '''

    POSSIBLE_PARAMETERS = frozenset(
        set(map(
            lambda k: '_'+k, _INJECTION_KEY_DEFAULTS))
        |{'optional'})


    _target_injection_keys = weakref.WeakKeyDictionary()

    def __new__(cls, target_, *, require_type = False, **constraints):
        assert (cls is InjectionKey) or set(constraints)-cls.POSSIBLE_PARAMETERS, "You cannot subclass InjectionKey with empty constraints"
        if require_type and not isinstance(target_, type):
            raise TypeError('Only types can be used as implicit injection keys; if this is intended then construct the injection key explicitly')
        if isinstance(target_, InjectionKey):
            # mostly so you can take an existing injection key and mark it optional
            new_constraints = dict(target_.constraints)
            new_constraints.update(constraints)
            constraints = new_constraints
            target_ = target_.target
        if (not constraints) :
            if  target_ in cls._target_injection_keys:
                return cls._target_injection_keys[target_]
        self =super().__new__(cls)
        customized = bool(constraints)
        if '_optional' not in constraints:
            try: constraints['_optional'] = constraints.pop('optional')
            except KeyError: pass
        for k in _INJECTION_KEY_DEFAULTS:
            self.__dict__[k] = constraints.pop(
                '_'+k,_INJECTION_KEY_DEFAULTS[k])


        self.__dict__['constraints'] = dict(constraints)
        self.__dict__['target'] = target_
        if (not customized)  and not isinstance(target_, (str, int, float)):
            cls._target_injection_keys[target_] = self
        return self


    def __getattr__(self,k):
        if k in self.__dict__: return self.__dict__[k]
        if k in self.__dict__['constraints']: return self.__dict__['constraints'][k]
        raise AttributeError

    def __repr__(self):
        r = "InjectionKey({}".format(
            self.target.__name__ if isinstance(self.target, type) else repr(self.target))
        for k,v in self.constraints.items():
            r += ",\n    {} = {}".format(
                repr(k), repr(v))
        return r+")"

    def __setattr__(self, k, v):
        raise TypeError('InjectionKeys are immutable')


    def __hash__(self):
        return hash(self.target)+sum([hash(k) for k in self.constraints.keys()])+sum([hash(v) for v in self.constraints.values()])

    def __eq__(self, other):
        if type(other) is not type(self): return False
        if self.target !=  other.target: return False
        if len(self.constraints) != len(other.constraints): return False
        if all(map(lambda k: self.constraints[k] == other.constraints[k], self.constraints.keys())):
            return True
        return False

    def supplementary_injection_keys(self, p):
        if (isinstance(p,type) and issubclass(p, Injectable)) or \
           isinstance(p, Injectable):
            yield from p.supplementary_injection_keys(self)
        else:
            if p.__class__ in (int, float, str,list, tuple, types.FunctionType):
                return
            for c in p.__class__.__mro__:
                if c is p.__class__: continue
                yield InjectionKey(c)

# Used in Injector.__init__
_injector_injection_key = InjectionKey(Injector)

@dataclass
class UnsatisfactoryDependency(RuntimeError):
    dependency: InjectionKey
    provider: DependencyProvider
    reason: str = None

def inject(**dependencies):
    '''A dhecorator to indicate that a function requires dependencies:

    Sample Usage::

        @inject(injector = Injector,
            router = InjectionKey(SiteRouter, site ='cambridge'))
        def testfn(injector, router): pass

    Can be applied to classes or functions.  Note that when an injector is used, dependencies will be resolved through the injector  even if they are supplied directly.  For example in::

        @inject(dependency = InjectedClass)
       def func(dependency):
             assert isinstance(dependency, InjectedClass)


        injector(func, dependency=InjectedClass)

    The assertion will be true if *InjectedClass* is an :class:`Injectable` because the injector will instantiate the class.  Resolution provided by the injector includes:

    #. Instantiating subclasses of :class:`Injectable` and providing their dependencies.

    #. Calling :meth:`~AsyncInjectable.async_ready` on :class:`AsyncInjectable`.

    '''
    def convert_to_key(deps):
        for k,v in deps.items():
            if isinstance(v, InjectionKey):
                yield k,v
            elif v is None: yield k,v
            else: yield k, InjectionKey(v, require_type = True)
    def init_from_bases(c, dependencies, autokwargs):
        for b in c.__bases__:
            if hasattr(b, "_injection_dependencies"):
                autokwargs -= b._injection_this_level
                dependencies.update(b._injection_dependencies)
                autokwargs |= b._injection_autokwargs
    def wrap(fn):
        if (not hasattr(fn, '_injection_dependencies')) or (isinstance(fn, type) and '_injection_dependencies' not in fn.__dict__):
            fn._injection_dependencies = dict()
            fn._injection_this_level = set()
            fn._injection_autokwargs = set()
            if isinstance(fn, type):
                init_from_bases(fn, fn._injection_dependencies, fn._injection_autokwargs)

        for k,v in convert_to_key(dependencies):
            try: fn._injection_autokwargs.remove(k)
            except KeyError: pass
            if v is not None:
                # So autokwargs doesn't include it
                fn._injection_this_level.add(k)
            fn._injection_dependencies[k] = v
        return fn
    return wrap


def inject_autokwargs(**dependencies):
    '''
    Like :func:`inject` but explicitly marks that the keywords are expected to fall through to :meth:`Injectable.__init__`
    Applies to all dependencies at the current level so can be used either like::

        @inject_autokwargs(foo = bar)
        class baz(Injectable):

    or like::

        @inject_autokwargs()
        @inject(foo = bar)
        class baz(Injectable):

    '''
    def wrap(cls):
        inject(**dependencies)(cls)
        cls._injection_autokwargs |= cls._injection_this_level
        return cls
    return wrap

def copy_and_inject(_wraps = None, **kwargs):
    "Like inject but makes a copy of the dependencies first; typically used when wrapping an injector"
    def wrap(fn):
        if hasattr(fn, '_injection_dependencies'):
            fn._injection_dependencies = fn._injection_dependencies.copy()
        return inject(**kwargs)(fn)
    if _wraps is not None:
        return wrap(_wraps)
    else: return wrap

Injector = inject(parent_injector = Injector)(Injector)

def partial_with_dependencies(func, *args, **kwargs):
    '''Partially aply arguments and keep injected dependencies

    Like :class:`functools.partial` except also preserves dependencies.
    Used typically when passing the result of *partial* to
    :meth:`Injector.add_provider`

    This implementation assumes that no dependencies are removed by
    passing arguments into partial that specify one of the injected
    dependencies.

    '''
    p = functools.partial(func, *args, **kwargs)
    try:
        p._injection_dependencies = func._injection_dependencies
    except AttributeError: pass
    return p

def directly_has_dependencies(f):
    '''

    :return: True if *f* directly has injection dependencies applied.  Not true for an object of a class even if that class has dependencies.

    '''
    if not hasattr(f, '__dict__'): return False
    return '_injection_dependencies' in f.__dict__


class dependency_quote:

    '''
Indicate that a value (or dependency provider) should not be subject to injcoter resolution.  Used like::

        injector(func, override = dependency_quote(self))

    For example if *self* is an object that is not yet :meth:`~AsyncInjectable.async_ready` as part of bootstrapping.  Can be used both in a keyword argument to an injector call or in calls to :meth:`Injector.add_provider`.

'''

    __slots__ = ['value']


    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f'dependency_quote({repr(self.value)})'

   #########################################
   # Asynchronous support:

@inject_autokwargs(
    injector = Injector)
class AsyncInjectable(Injectable):

    '''

    An :class:`Injectable` that supports asyncronous operations as part of making a dependency available.  This happens in several phases:

    * Prior to construction, all the dependencies of the *Injectable* are prepared.

    * :meth:`async_resolve` is called.  This asynchronous method can return a different object, which entirely replaces this object as the provider of the dependency.  The *async_resolve* protocol is intended for cases where figuring out which object will provide a dependency requires asynchronous operations.  In many cases :meth:`async_resolve` returns *self*.

    * Call :meth:`async_ready` to prepare this object.  This may include doing things like running :func:`~carthage.setup_tasks.setup_task`.

'''

    _async_ready_state: ReadyState

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # superclass claims the injector for us.
        if hasattr(self, 'injector'):
            self.ainjector = self.injector(AsyncInjector)
        if self.async_resolve.__func__ is AsyncInjectable.async_resolve:
            if self.async_ready.__func__ is AsyncInjectable.async_ready:
                self._async_ready_state = ReadyState.READY
            else: self._async_ready_state = ReadyState.RESOLVED
        else: #has nontrivial async_resolve
            self._async_ready_state = ReadyState.NOT_READY


    async def async_ready(self):
        self._async_ready_state = ReadyState.READY
        return self

    async def async_resolve(self):
        '''Returns None or an object that should replace *self* in providing dependencies.'''
        return None

    async def async_become_ready(self):
        if self._async_ready_state == ReadyState.NOT_READY:
            raise RuntimeError("Resolution should have already happened")
        elif self._async_ready_state == ReadyState.RESOLVED:
            self._ready_future = asyncio.ensure_future(self.async_ready())
            self._async_ready_state = ReadyState.READY_PENDING
            try:
                return await self._ready_future
            except:
                self._async_ready_state = ReadyState.RESOLVED
                raise
            finally: del self._ready_future
        elif self._async_ready_state == ReadyState.READY_PENDING:
            return await asyncio.shield(self._ready_future)
        else: return



@inject(loop = asyncio.AbstractEventLoop, injector = Injector)
class AsyncInjector(Injectable):

    '''An asynchronous injector.  AsyncInjector is not a subclass of
    Injector because AsyncInjector's call function is a coroutine and
    so it has an incompatible interface.  In other ways the classes
    should behave the same.

    This class overrides :class:`Injectable`'s behavior of claiming the injector.  Instead, if you construct an *AsyncInjector* you get exactly what you asked for: an *AsyncInjector* that maps directly onto the injector you construct.  Note however that when an *AsyncInjector* is constructed by :class:`AsyncInjectable`, the injector is claimed properly.

'''

    def __init__(self, injector, loop):
        self.injector = injector
        self.injector.replace_provider(self)
        self.loop = loop
        # For methods that injector has but we do not, then call the method on our injector.  This is a lot like inheritance but does not make us a subclass.
        for k in list(Injector.__dict__.keys()) + list(event.EventListener.__dict__.keys()):
            if  not isinstance(getattr(Injector, k), types.FunctionType):
                continue

            if hasattr(self, k): continue
            setattr(self, k, getattr(self.injector, k))

    def claim(self, claimed_by = True):
        if self.injector.is_claimed:
            return type(self)(injector = self.injector.claim(claimed_by),
                              loop = self.loop)
        else:
            assert self.injector.claim(claimed_by) is self.injector
            return self

    def __repr__(self):
        return f'<Async Injector Injector: {repr(self.injector)}>'

    def __contains__(self, k):
        return k in self.injector


    async def __call__(self, cls, *args, **kwargs):
        '''Coroutine to Construct an instance of cls using the providers in this injector.
        Instantiate providers as needed.  In general a sub-injector is not
        constructed.  However if any keyword arguments pased in specify a
        dependency, then construct an injector for that.  Keyword arguments
        and arguments are passed to the class to construct the object.  If
        keyword arguments do specify a dependency, they must satisfy the
        InjectionKey involved.
'''
        if not hasattr(self, 'loop'):
            self.loop = self.get_instance(asyncio.AbstractEventLoop)
        res =  self._instantiate(
            cls, *args, **kwargs,
            _loop = self.loop,
            _placement = None,
            _interim_placement = None,
            _orig_k = None)

        if isinstance(res, asyncio.Future):
            return await res
        else: return res

    async def get_instance_async(self, k):
        futures = []
        res = self.get_instance(k,
                                loop = self.loop,
                                futures = futures)
        if isinstance(res, (asyncio.Future, collections.abc.Coroutine)):
            return await res
        else: return res

    async def filter_instantiate_async(self, target, predicate,
                                       *, stop_at = None,
                                       ready = None):

        '''
        Like :meth:`filter_instantiate` except in an async context.  With *filter_instance* objects are not-ready by default.  With *filter_instance_async*, the ready state is default (true unless in the middle of instantiating something with ready false).

        '''
        results = []
        result_keys = []
        for k in self.filter(target, predicate, stop_at = stop_at):
            if ready is not None: k = InjectionKey(k, _ready = ready)
            results.append(self.get_instance_async(k))
            result_keys.append(k)
        results = await asyncio.gather(*results)
        zipped = zip(result_keys, results)
        return [z for z in zipped if z[1] is not None]



# Injector cross reference support
class InjectorXrefMarkerMeta(type):

    def __repr__(self):
        return f'injector_xref({self.injectable_key}, {self.target_key})'


class InjectorXrefMarker(AsyncInjectable, metaclass = InjectorXrefMarkerMeta):

    # A separate subclass is created for each injector_xref with one
    # class attributes: target_key.  If possible our __new__ entirely
    # resolves to target_key and simply returns that object.  If that
    # requires asynchronous action, then we actually instantiate
    # ourself and depend on async_resolv.  We receive an injectable
    # through dependency injection.

    def __new__(cls, injectable):
        if isinstance(injectable, Injector):
            injector = injectable
        else: injector = injectable.injector
        try:
            res = injector.get_instance(cls.target_key)
            return res
        except AsyncRequired:
            # Don't pass in the injector; we don't want to claim it
            self = super().__new__(cls)
            self.ainjector = injector.get_instance(AsyncInjector)
            return self

    @classmethod
    def supplementary_injection_keys(cls, k):
        return
        yield None #to make it a generator

    @classmethod
    def satisfies_injection_key(cls, k):
        target = cls.target_key.target
        if (isinstance(target, type) and issubclass(target, Injectable)) or \
           isinstance(target, Injectable):
            return target.satisfies_injection_key(k)
        return True

    async def async_resolve(self):
        return await self.ainjector.get_instance_async(self.target_key)

def injector_xref(injectable_key: InjectionKey,
                  target_key: InjectionKey,
                  ):

    '''

    Request that one injector look up a target in another injector.  Typical usage::

        base_injector.add_provider(target_key,
            injector_xref(sub_injector_key, target_key))

    :param injectable_key: The :class:`InjectionKey` of an :class:`Injectable` or an :class:`Injector` in which the target is actually looked up.

    :param target_key: An :class:`InjectionKey` registered with the
    injector belonging to *injectable_key*.  It is important that
    *target_key* actually be provided by that injector.  In the common
    case where the parent of *injectable_key* eventually chains back
    to the injector in which this *injector_xref* is provided, a loop
    can happen otherwise.

    '''
    tkey = target_key
    ikey = injectable_key
    @inject(injectable = injectable_key,
            injector = None)
    class instance(InjectorXrefMarker):
        target_key = tkey
        injectable_key = ikey
    return instance



async def shutdown_injector(injector, timeout = 5):

    '''
    Close an injector and cancel running tasks


    This closes an injector, canceling any running tasks.  It waits up to *timeout* seconds for any canceled tasks to terminate.

'''
    canceled_futures = []
    injector.close(canceled_futures = canceled_futures)
    if canceled_futures:
        await asyncio.wait(canceled_futures, timeout = timeout)

def _call_close(obj, canceled_futures):
    if not hasattr(obj, 'close'): return
    sig = inspect.signature(obj.close)
    try:
        if 'canceled_futures' in sig.parameters:
            return obj.close(canceled_futures = canceled_futures)
        else: return obj.close()
    except TypeError: pass #calling on not yet constructed class

def aspect_for( cls: typing.Type[Injectable],
               property: str):
    '''A decorator for a class indicating that the class provides an
    optional dependency to another class.  Once decorated, newly
    created instances of *cls* may have a property *property* if
    *cls*'s :class:`Injector` is able to instantiate the decorated
    class.  Usage might be something like::

        @aspect_for(Machine, 'model')
        class MachineModel:
        . . .

    Then in code that is given a machine you can do things like::

        try:
            os = machine.model.os
        except AttributeError: os = "unknown"

    In a dependency injection framework, it is common for a middle
    layer not to care about domain-specific knowledge that is needed
    both in an interface layer and in domain-specific implementation
    code.  As an example, implementations of
    :class:`~carthage.container.Container` don't care much what
    operating system the container is running.  However user-interface
    code to configure the container might need to set what operating
    system is desired, and various domain-specific
    :class:`~carthage.machine.BaseCustomization` classes may need to
    change behavior based on the operating system.  Aspects provide a
    convenient syntax for the injector hierarchy to be used to access
    this information.

    '''
    def wrapper(val):
        raise NotImplementedError("This approach is not great because of subclasses")
        if property in cls._injection_dependencies:
            raise TypeError( f'{k} is already a dependency for {cls.__name__}.')
        inject(property = val)(cls)
        return val
    return wrapper


__all__ = '''
    inject inject_autokwargs Injector AsyncInjector
    Injectable AsyncInjectable InjectionFailed ExistingProvider
    InjectionKey AsyncRequired
    DependencyProvider
aspect_for
dependency_quote injector_xref
    partial_with_dependencies shutdown_injector
'''.split()
