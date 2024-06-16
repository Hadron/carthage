# Copyright (C) 2018, 2019, 2020, 2021, 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import contextlib
import contextvars
import enum
import inspect
import typing
import weakref
import collections.abc
import asyncio
import functools
import logging
import traceback
import types
import sys
from dataclasses import dataclass
from .. import tb_utils, event
from .introspection import *
from ..utils import NotPresent

_chatty_modules = {asyncio.futures, asyncio.tasks, sys.modules[__name__]}
logger = logging.getLogger('carthage.dependency_injection')
logger.setLevel('INFO')

#: If true, tracebacks are filtered for better user error messages, but hinding internal state
filter_tracebacks = True


class ReadyState(enum.Enum):
    NOT_READY = 0
    RESOLVED = 1
    READY_PENDING = 2
    READY = 3


instantiate_to_ready = contextvars.ContextVar('instantiate_to_ready', default=True)

log_injection_failed = contextvars.ContextVar('log_injection_failed', default=True)

# While this is needed by InjectableModelType's add_provider, it is
# not part of the public api


def default_injection_key(p):
    if isinstance(p, DependencyProvider):
        raise NotImplementedError
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
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Call inject to deal better with multiple inheritance and dependencies
        try: inject()(cls)
        except NameError:
            if 'inject' not in globals():
                #inject not yet defined
                return
            raise

    def __init__(self, *args, **kwargs):
        autokwargs = set(getattr(self, '_injection_autokwargs', set()))
        for k, d in getattr(self, '_injection_dependencies', {}).items():
            if k in kwargs:
                if d is _injector_injection_key:
                    injector = kwargs.pop(k)
                    setattr(self, k, injector.claim(self))
                else:
                    setattr(self, k, kwargs.pop(k))
                try:
                    autokwargs.remove(k)
                except KeyError:
                    pass

        if autokwargs:
            raise TypeError(f'The following dependencies were not specified: {autokwargs}')

        try:
            super().__init__(*args, **kwargs)
        except TypeError as t:
            if 'object.__init__()' in str(t):
                raise TypeError(f'The following extra arguments were specified: {list(kwargs.keys())}')
            raise

    def close(self, canceled_futures=None):
        if hasattr(self, 'injector'):
            self.injector.close(canceled_futures)

    @classmethod
    def supplementary_injection_keys(cls, k: InjectionKey):
        '''
        Returns  an iteration of :class:`InjectionKeys <InjectionKey>` that should be added  to an injector when this class is added.  The current injection key is taken as an argument so that constraints applied can modify what keys are added.


        '''
        for c in cls.__mro__:
            if c in (Injectable, AsyncInjectable):
                continue
            if issubclass(c, Injectable) and c != k.target:
                yield InjectionKey(c)
                if k.constraints:
                    yield InjectionKey(c, **k.constraints)
            elif c is k.target and k.constraints:
                yield InjectionKey(c)

    @classmethod
    def satisfies_injection_key(cls, k):
        if k == InjectionKey(cls):
            return True
        if isinstance(k.target, (str, tuple)):
            return True
        return issubclass(cls, k.target)

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
                 'instantiation_contexts',
                 'keys',
                 '_creation_tb',
                 )

    def __init__(self, provider, allow_multiple=False, close=True):
        self.provider = provider
        self.allow_multiple = allow_multiple
        self.close = close
        self.keys = set()
        self.instantiation_contexts = set()
        self._creation_tb = traceback.extract_stack()[:-1]

    def __repr__(self):
        return "<DependencyProvider allow_multiple={}: {}>".format(
            self.allow_multiple, repr(self.provider))

    @property
    def is_factory(self):
        return (isinstance(self.provider, type) and issubclass(self.provider, Injectable)) \
            or asyncio.iscoroutinefunction(self.provider) \
            or directly_has_dependencies(self.provider)

    @property
    def needs_quote(self):
        return self.is_factory \
            or asyncio.isfuture(self.provider) \


    def record_instantiation(self, instance, k, satisfy_against, final):
        dp = satisfy_against._providers.setdefault(
            k, DependencyProvider(
                instance, self.allow_multiple, close=self.close))
        assert dp.needs_quote or dp.provider is instance
        dp.provider = instance
        if final:
            # If requested again, would try to instantiate again.
            if dp.needs_quote:
                dp.provider = dependency_quote(dp.provider)
        return dp


class InjectionFailed(RuntimeError):

    def __init__(self, context):
        super().__init__(f"Error {str(context)}")
        ctx = context
        dependency_path = []
        while ctx:
            try:
                dependency_path.insert(0,ctx.key)
                ctx = ctx.parent
            except AttributeError:
                ctx = ctx.parent
        try:
            self.failed_dependency = dependency_path[-1]
        except IndexError: pass
        self.dependency_path = tuple(dependency_path)
        

class ExistingProvider(RuntimeError):

    def __init__(self, k, old_p, new_p):
        self.existing_key = k
        self.old_provider = old_p
        self.new_provider = new_p
        s = f'Unable to add provider {new_p.provider} for {k}: already registered to {old_p.provider}.\n\n'
        s = s + 'The previous provider was created here:\n\n'
        s = s + '> ' + '> '.join(traceback.format_list(old_p._creation_tb))
        super().__init__(s)


class InjectorClosed(RuntimeError):
    pass


class AsyncRequired(RuntimeError):

    def __init__(self, msg, context):
        super().__init__(f'{msg} {context.description if context else ""}')


# Note that after @inject is defined, this class is redecorated to take parent_injector as a dependency so that
#    injector = sub_injector(Injector)
# works
class Injector(Injectable, event.EventListener):

    def __init__(self, *providers,
                 parent_injector=None):
        self._providers = {}
        self._pending = weakref.WeakSet()
        self.closed = False
        self._closing = False
        if parent_injector is None and len(providers) > 0:
            if isinstance(providers[0], Injector):
                parent_injector = providers[0]
                providers = providers[1:]

        self.parent_injector = parent_injector
        self.claimed_by = None
        if self.parent_injector:
            event_scope = self.parent_injector._event_scope
            event_scope.add_child(parent_injector, self)
        else:
            event_scope = None
        super().__init__(event_scope=event_scope)
        for p in providers:
            self.add_provider(p)
        self.add_provider(self)  # Make sure we can inject an Injector
        self.add_provider(InjectionKey(AsyncInjector), AsyncInjector, allow_multiple=True)

    def claim(self, claimed_by=True):
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
            else:
                self.claimed_by = weakref.ref(claimed_by)
            return self

    def add_provider(self, k, p=None, *,
                     allow_multiple=False,
                     close=True,
                     replace=False):
        '''Add a provider for a dependency

        Either called as ``add_provider(provider)`` or
        ``add_provider(injection_key, provider)``\\ .  In the first form, a key is
        automatically constructed.  For :class:`Injectable` objects, see :meth:`Injectable.default_instance_injection_key()`.  For *Injectable* types, see :meth:`Injectable.default_class_injection_key()`.  For other objects, the unconstrained injection key for types (or for the type of the object) is used.

        :param allow_multiple: If true, then this provider may be instantiated multiple times in sub-injectors.  If false (the default) then the provider will be instantiated on the injector where it is added and used by all sub-injectors.

        :param close: If true (the default), then closing the injector will close or cancel this provider.  If false, then the provider will not be deallocated.  As an example, if the :class:`asyncio.AbstractEventLoop` is added as a provider, but closing this injector should not close the loop and end all async operations, then close can be set to false.

        :param replace: If True, an existing provider is being updated.  :meth:`replace_provider` is a convenience function for calling :meth:`add_provider` with *replace* set to True.  Replacing providers may lead to inconsistent results if the provider has already been injected to fill a dependency in a constructed object.

'''
        if p is None and not isinstance(k, InjectionKey):
            p, k = k, p  # swap; we construct the key later

        if k is None:
            k = default_injection_key(p)
        if not isinstance(p, DependencyProvider):
            p = DependencyProvider(p, allow_multiple=allow_multiple, close=close)
        assert isinstance(k, InjectionKey)
        if k in self:
            if p is self._get(k):
                return k
            existing_provider = self._get(k)
            if replace:
                existing_provider.provider = p.provider
                existing_provider.keys.add(k)
            else:
                raise ExistingProvider(k, existing_provider, p)
        else:
            self._providers[k] = p
            p.keys.add(k)
        for k2 in k.supplementary_injection_keys(p.provider):
            if k2 not in self:
                self._providers[k2] = p
                p.keys.add(k2)
        self.emit_event(
            k, "add_provider",
            p.provider,
            replace=replace, close=close,
            allow_multiple=allow_multiple,
            inspector=InjectedDependencyInspector(injector=self, key=k, provider=p),
            other_keys=p.keys,
            adl_keys=p.keys | {InjectionKey(Injector)})
        return k

    def replace_provider(self, *args, **kwargs):
        return self.add_provider(*args, **kwargs, replace=True)

    def _get(self, k):
        return self._providers[k]

    def _get_parent(self, k):
        # Returns  DependencyProvider, instantiation_target
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
        if injector and (k in injector):
            return injector
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
               predicate: typing.Union[list, typing.Callable],
               stop_at: Injector = None):
        ''':return: list of :class:`InjectionKey` with target type of
            *target* and satisfying *predicate* in the current injector
            and its parents.

        :param predicate: A list of constraints that must all be
        present in the key, or a callable that returns true if the key
        should be included. The predicate is a mandatory argument;
        while it is possible to pass in something like ``lambda k:
        True`` to get all keys, this is only likely to be desirable
        when writing inspection code to examine the injection system.
        Supplementary injection keys and third party plugins will add
        InjectionKeys using targets in manners unexpected by the
        target class.  When filtering for a target it is best to have
        a constraint with a well-defined meaning in the context of
        that class; as an example :class:`~carthage.Machine` uses the
        *host* constraint for a FQDN.

        :param target: A target type to filter against. This can be
        *None* to filter against all targets.  Again, that is likely
        to be useful only in inspection logic.

        :param stop_at: An injector which must be a parent of this injector.  Do not progress past that injector in finding keys.  So if *stop_at* is *self*, only locally registered keys are returned.


        Example usage would be to find all registered plugins similar to the following::

            plugin_keys =injector.filter(CarthagePlugin, ['name'])

        '''
        def filter_for_constraints(k):
            return all(map(lambda c: c in k.constraints, constraints))
        if isinstance(predicate, list):
            constraints = predicate
            predicate = filter_for_constraints
        if isinstance(stop_at, AsyncInjector):
            stop_at = stop_at.injector
        assert isinstance(stop_at, (Injector, type(None)))
        if stop_at == self:
            result = {}  # stop here
        elif stop_at and not self.parent_injector:
            raise ValueError(f'{stop_at} was not in the parent chain')
        elif self.parent_injector:
            result = {k: True for k in self.parent_injector.filter(target, predicate, stop_at=stop_at)}
        else:  # no stop_at; ended chain
            result = {}
        result.update({k: True for k in self._providers.keys() if ((not target) or   k.target is target ) and predicate(k)})
        return list(result.keys())

    def filter_instantiate(self, target, predicate, *, stop_at=None, ready=False):
        '''
        Like :meth:`filter` but an iterator returning tuples of keys instance.
'''
        for k in self.filter(target, predicate, stop_at=stop_at):
            if ready is not None:
                k = InjectionKey(k, _ready=ready)
            res = self.get_instance(k)
            if res is not None:
                yield k, res

    def inspect(self, *, key_filter=None, include_parent:bool = False):
        '''
        Inspect the contents of this injector.
        This is a generator yielding key, :class:`~InjectedDependencyInspector` pairs for each dependency provided by the injector.

        :param key_filter: A function that takes a key and returns true if  the inspector should inspect the dependency for this key.

        :param include_parent: By default only this injector is  covered; if True, then the inspection recurses into the parent.

        '''
        if key_filter:
            already_included = set(filter(key_filter, self._providers.keys()))
            parent_filter = lambda k: k not in already_included and key_filter(k)
        else:
            already_included = set(self._providers.keys())
            parent_filter = lambda k: k not in already_included
        yield from ((k, InjectedDependencyInspector(
            key=k, injector=self, provider=self._providers[k]
            )) for k in already_included)
        if include_parent and self.parent_injector: 
            yield from self.parent_injector.inspect(
                key_filter=parent_filter, include_parent=True)

    @property
    def child_injectors(self):
        return frozenset(self._event_scope.children[id(self)])
    
            

            
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
            _loop=None,
            _placement=None,
            _interim_placement=None)

    def get_instance(self, k,
                     placement=None,
                     loop=None, futures=None):
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
            provider.record_instantiation(res, k, satisfy_against, final=True)
            instantiation_context.final()
            if placement:
                placement(res)

        def do_interim_place(res):
            provider.record_instantiation(res, k, satisfy_against, final=False)
            instantiation_context.progress()

        logger.debug("Looking up provider for {}".format(k))

        if not isinstance(k, InjectionKey):
            k = InjectionKey(k)
        try:
            provider, satisfy_against = self._get_parent(k)
        except KeyError:
            self._check_closed()
            if k.optional:
                res = None if k.optional is True else k.optional
                if placement:
                    placement(res)
                return res
            raise KeyError("No dependency for {}".format(k)) from None
        mark_instantiation_done = True
        with InstantiationContext(
                satisfy_against, self, k, provider,
                k.ready if (k.ready is not None) else instantiate_to_ready.get()) as instantiation_context:
            try:
                if k.ready is not None:
                    ready_reset = instantiate_to_ready.set(k.ready)
                else:
                    ready_reset = None
                to_ready = instantiate_to_ready.get()
                result = provider.provider
                if isinstance(result, dependency_quote):
                    if placement:
                        placement(result.value)
                    return result.value
                elif isinstance(result, asyncio.Future) \
                        or (not provider.is_factory and to_ready and not is_obj_ready(result)):
                    # We need to handle the async object through
                    # _handle_async; if it is a future it may resolve
                    # to an AsyncInjectable, and if it needs to become
                    # ready we need to allow for that.  We also need
                    # to set up placement.
                    if loop is None:
                        raise AsyncRequired(
                            f'{k} requires asynchronous instantiation',
                            current_instantiation())
                    result = loop.create_task(self._handle_async(
                        result,
                        placement=do_place, loop=loop,
                        mark_instantiation_done=True, instantiation_context=instantiation_context))
                    mark_instantiation_done = False
                    result.set_name('handle ' + str(current_instantiation()))
                    futures.append(result)
                    instantiation_context.progress()
                    return result
                elif provider.is_factory:
                    result = satisfy_against._instantiate(
                        result,
                        _loop=loop,
                        _placement=do_place,
                        _interim_placement=do_interim_place,
                        _instantiation_context=instantiation_context,
                    )
                if isinstance(result, asyncio.Future):
                    if loop is None:
                        raise AsyncRequired(
                            f'{k} requires asynchronous instantiation',
                            current_instantiation())
                    futures.append(result)
                    provider.record_instantiation(result, k, satisfy_against, final=False)
                    instantiation_context.progress()
                    mark_instantiation_done = False
                    return result

            finally:
                if ready_reset is not None:
                    instantiate_to_ready.reset(ready_reset)
                if mark_instantiation_done:
                    instantiation_context.done()

            # Either not a future or not a factory
            if placement:
                placement(result)
            return result

    def _instantiate(self, cls, *args,
                     _loop,
                     _placement,
                     _interim_placement,
                     _instantiation_context=None,
                     **kwargs):
        # _loop if  present means we can return something for which _is_async will return True
        # There are complicated interactions between the
        # DependencyProvider machinery and the InstantiationContext
        # machinery.  An instantiationcontext is marked done when  the
        # instantiation has completed or failed and no other events
        # will be logged against this context.  A DpendencyProvider is
        # recorded as final when the underlying object providing the
        # dependency will no longer change.  That doesn't mean the
        # instantiation is done though.  The most common case where a
        # DependencyProvider can be final but where the instantiation
        # is not done is where async_become_ready needs to be
        # called.
        self._check_closed()

        def handle_result(mark_instantiation_done):
            # Called when all kwargs are populated
            try:
                try:
                    res = cls(*args, **kwargs)
                except TypeError as e:
                    raise TypeError(f'Error constructing {cls}:') from e
                if self._is_async(res):
                    if not _loop:
                        if isinstance(res, collections.abc.Coroutine):
                            res.close()
                        raise AsyncRequired(
                            "Asynchronous dependency injected into non-asynchronous context",
                            current_instantiation())

                    future = asyncio.ensure_future(self._handle_async(
                        res,
                        placement=_placement,
                        loop=_loop, mark_instantiation_done=mark_instantiation_done,
                        instantiation_context=_instantiation_context))
                    if _interim_placement:
                        _interim_placement(future)
                    self._pending.add(future)

                    if _instantiation_context:
                        future.set_name(f'async {_instantiation_context.description}')
                    else:
                        future.set_name(f'Handle async  {res} for {self}')
                    return future
                else:
                    if _placement:
                        _placement(res)
                    return res
            except AsyncRequired:
                raise
            except Exception as e:
                if filter_tracebacks:
                    tb_utils.filter_chatty_modules(e, _chatty_modules, None)
                if _instantiation_context:
                    failed_instantiation(_instantiation_context, e)
                if _instantiation_context and not isinstance(e, InjectionFailed):
                    tb_utils.filter_before_here(e)
                    if log_injection_failed.get():
                        logger.exception(f'Error resolving dependency for {current_instantiation()}')
                    raise InjectionFailed(current_instantiation()) from e
                else:
                    raise

        async def callback(futures):
            try:
                await asyncio.gather(*futures)
                # since that succeeded, allkwargs are placed
                res = handle_result(mark_instantiation_done=False)
                if isinstance(res, asyncio.Future):
                    return await res
                return res
            finally:
                if _instantiation_context:
                    _instantiation_context.done()

        def kwarg_place(k):
            def collect(res):
                if res is not NotPresent:
                    kwargs[k] = res
            return collect
        try:
            futures = []

            dks = set(filter(
                lambda k: cls._injection_dependencies[k] is not None,
                cls._injection_dependencies.keys()))
        except AttributeError:
            dks = set()
        injector = self  # or sub_injector if created
        sub_injector = None
        kwarg_dependencies = set(kwargs.keys()) & dks
        try:  # clean up sub_injector
            if kwarg_dependencies:
                sub_injector = (type(self))(self)
                injector = sub_injector
                for k in kwarg_dependencies:
                    provider = kwargs.pop(k)
                    dependency = cls._injection_dependencies[k]
                    if isinstance(provider, Injectable) and not provider.satisfies_injection_key(dependency):
                        raise UnsatisfactoryDependency(dependency, provider)
                    sub_injector.add_provider(dependency, provider, close=False)
            for k, d in (cls._injection_dependencies.items()) if dks else []:
                if d is None:
                    continue
                try:
                    injector.get_instance(d, placement=kwarg_place(k),
                                      loop=_loop, futures=futures)
                except KeyError as e:
                    if _instantiation_context:
                        raise InjectionFailed(current_instantiation()) from e
                    else:
                        raise
            if futures:
                fut = asyncio.ensure_future(callback(futures))
                if current_instantiation():
                    fut.set_name(f'{current_instantiation()}')
                else:
                    fut.set_name(f'Instantiate {cls} for {self}')
                return fut

            else:
                res = handle_result(mark_instantiation_done=(_instantiation_context is not None))
                return res
        finally:
            # Perhaps some day we need to clean up something about the sub_injector
            pass

    def _is_async(self, p):
        if isinstance(p, (collections.abc.Coroutine,
                          asyncio.Future)):
            return True
        elif isinstance(p, AsyncInjectable) and p._async_ready_state != ReadyState.READY:
            to_ready = instantiate_to_ready.get()
            return (p._async_ready_state == ReadyState.NOT_READY) or to_ready
        return False

    async def _handle_async(self, p,
                            placement,
                            loop,
                            instantiation_context=None,
                            mark_instantiation_done=True):
        if not hasattr(self, 'loop'):
            self.loop = loop
        try:
            if isinstance(p, collections.abc.Coroutine):
                res = await p
            elif isinstance(p, AsyncInjectable):
                res = await self._handle_async_injectable(p, placement=placement,
                                                          mark_instantiation_done=False, instantiation_context=instantiation_context)
            elif isinstance(p, asyncio.Future):
                # We may need to re-add an instantiation to
                # waiting dependencies in the instantiations that
                # are moving to ready.
                if mark_instantiation_done:
                    current_instantiation().progress()
                res = await p
                if isinstance(res, AsyncInjectable) and res._async_ready_state != ReadyState.READY:
                    res = await self._handle_async_injectable(
                        res, placement=placement,
                        mark_instantiation_done=False, instantiation_context=instantiation_context)
            else:
                raise RuntimeError('_is_async returned True when _handle_async cannot handle')
            if placement:
                placement(res)
            return res
        except asyncio.CancelledError:
            if hasattr(p, 'injector'):
                await shutdown_injector(p.injector)
            raise
        except Exception as e:
            if filter_tracebacks:
                tb_utils.filter_chatty_modules(e, _chatty_modules, None)
            if instantiation_context:
                failed_instantiation(instantiation_context, e)
            if instantiation_context and not isinstance(e, InjectionFailed):
                tb_utils.filter_before_here(e)
                if log_injection_failed.get():
                    logger.exception(f'Error resolving dependency for {current_instantiation()}')
                raise InjectionFailed(instantiation_context) from e
            else:
                raise

        finally:
            if mark_instantiation_done and current_instantiation():
                current_instantiation().done()

    async def _handle_async_injectable(self, obj, placement, resolv=True,
                                       instantiation_context=None, mark_instantiation_done=False):
        try:
            # Don't bother running the resolve protocol for the base case
            if resolv and (obj._async_ready_state == ReadyState.NOT_READY):
                res = await obj.async_resolve()
                if res is None or res is obj:
                    if obj._async_ready_state == ReadyState.NOT_READY:
                        obj._async_ready_state = ReadyState.RESOLVED
                    res = obj
                if self._is_async(res):
                    return await self._handle_async(
                        res, placement=placement,
                        loop=self.loop,
                        mark_instantiation_done=False, instantiation_context=instantiation_context)
                else:
                    return res
            else:  # no resolution required
                if instantiate_to_ready.get():
                    if placement:
                        placement(obj)
                    await obj.async_become_ready()
                    if not obj._async_ready_state == ReadyState.READY:
                        raise RuntimeError(
                            f"async_ready for {obj.__class__.__name__} must chain back to AsyncInjectable.async_ready.")

                return obj
        except asyncio.CancelledError:
            if hasattr(obj, 'injector'):
                await shutdown_injector(obj.injector)
            raise
        finally:
            if mark_instantiation_done:
                current_instantiation().done()

    def close(self, canceled_futures=None):
        '''
        Close all subinjectors or providers

        For every provider registered with this injector, call :meth:`close` if it is exists.  Then clear out all providers.  Note that this will also close sub-injectors of providers.

        If using `AsyncInjector`, it is better to call :func:`shutdown_injector` to cancel any running asynchronous tasks.

        If the provider's :meth:`close` method takes an argument called *canceled_futures* then the *canceled_futures* argument will be passed down.
        '''

        for f in self._pending:
            try:
                f.cancel()
            except BaseException:
                pass
        self._pending.clear()
        if self._closing:
            return
        self._closing = True
        providers = list(self._providers.values())
        for p in providers:
            if p.provider is self or not p.close:
                continue
            if hasattr(p.provider, 'close'):
                try:
                    _call_close(p.provider, canceled_futures)
                except Exception:
                    logger.exception("Error closing {}".format(p))
            elif asyncio.isfuture(p.provider):
                p.provider.cancel()
                if canceled_futures is not None:
                    canceled_futures.append(p.provider)
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
        else:
            try:
                claim_str = f'claimed by {repr(self.claimed_by())}'
            except Exception:
                claim_str = f'claimed by object with id {id(self.claimed_by())}'
        closed_str = 'CLOSED ' if self.closed else ''
        return f'<{closed_str}{self.__class__.__name__} {claim_str}>'

    @property
    def is_claimed(self):
        return self.claimed_by is not None


_INJECTION_KEY_DEFAULTS = {
    'optional': False,
    'globally_unique': False,
    'ready': None}


class InjectionKey:

    '''
    Represents information about what is requested to satisfy a dependency.

    :param target: A type or other object representing what is desired.

        * A type indicating an object of that type is desired

        * An object such as a string that is a unique identifier for what is desired

    :param _optional: If *True*, then if no provider for the dependency is registered, None will be passed rather than raising; if some other true value, that value will be returned by default.  If *NotPresent*, then no kwarg will be specified if the dependency is not provided. Because of the historical API, there is no current way to have an optional key default to *True*.  The *optional* parameter is read from the call to :meth:`Injector.add_provider`.

    :param _ready:  If None (the default), then use the same readyness as the object into which this is being injected (or full readyness if this is a base operation).  If True, then to satisfy this dependency, the provided object must be fully ready.  If False, then a not ready object is preferred.

    :param _globally_unique:  If true, then this provided dependency (typically in a call to :func:`carthage.modeling.provides`) is globally unique and need not be modified during :class:`container propagation <carthage.modeling.ModelingContainer>`.

    '''

    POSSIBLE_PARAMETERS = frozenset(
        set(map(
            lambda k: '_' + k, _INJECTION_KEY_DEFAULTS))
        | {'optional'})

    _target_injection_keys = weakref.WeakKeyDictionary()

    def __new__(cls, target_, *, require_type=False, **constraints):
        assert (cls is InjectionKey) or set(constraints) - \
            cls.POSSIBLE_PARAMETERS, "You cannot subclass InjectionKey with empty constraints"
        if require_type and not isinstance(target_, type):
            raise TypeError(
                'Only types can be used as implicit injection keys; if this is intended then construct the injection key explicitly')
        for k in constraints:
            if k.startswith('_') and k not in cls.POSSIBLE_PARAMETERS:
                raise TypeError(f'{k} is not an InjectionKey parameter')
        if isinstance(target_, InjectionKey):
            # mostly so you can take an existing injection key and mark it optional
            new_constraints = dict(target_.constraints)
            new_constraints.update(constraints)
            constraints = new_constraints
            target_ = target_.target
        if (not constraints):
            if target_ in cls._target_injection_keys:
                return cls._target_injection_keys[target_]
        self = super().__new__(cls)
        customized = bool(constraints)
        if '_optional' not in constraints:
            try:
                constraints['_optional'] = constraints.pop('optional')
            except KeyError:
                pass
        for k in _INJECTION_KEY_DEFAULTS:
            self.__dict__[k] = constraints.pop(
                '_' + k, _INJECTION_KEY_DEFAULTS[k])

        self.__dict__['constraints'] = dict(constraints)
        self.__dict__['target'] = target_
        if (not customized) and not isinstance(target_, (str, int, float)):
            cls._target_injection_keys[target_] = self
        return self

    def __getattr__(self, k):
        if k in self.__dict__:
            return self.__dict__[k]
        if k in self.__dict__['constraints']:
            return self.__dict__['constraints'][k]
        raise AttributeError

    def __repr__(self):
        r = "InjectionKey({}".format(
            self.target.__name__ if isinstance(self.target, type) else repr(self.target))
        if self.ready is not None:
            r += f', _ready={self.ready}'
        for k, v in self.constraints.items():
            r += f', {str(k)}={repr(v)}'
        return r + ")"

    def __setattr__(self, k, v):
        raise TypeError('InjectionKeys are immutable')

    def __hash__(self):
        return hash(self.target) + sum([hash(k) for k in self.constraints.keys()]) + \
            sum([hash(v) for v in self.constraints.values()])

    def __eq__(self, other):
        if not isinstance(other, type(self)):
            return False
        if self.target != other.target:
            return False
        if len(self.constraints) != len(other.constraints):
            return False
        if all(map(lambda k: k in other.constraints and self.constraints[k] == other.constraints[k], self.constraints.keys())):
            return True
        return False

    def supplementary_injection_keys(self, p):
        if (isinstance(p, type) and issubclass(p, Injectable)) or \
           isinstance(p, Injectable):
            yield from p.supplementary_injection_keys(self)
        else:
            if p.__class__ in (int, float, str, list, tuple, types.FunctionType):
                return
            for c in p.__class__.__mro__:
                if c is p.__class__:
                    continue
                yield InjectionKey(c)


# Used in Injector.__init__
_injector_injection_key = InjectionKey(Injector)


@dataclass
class UnsatisfactoryDependency(RuntimeError):
    dependency: InjectionKey
    provider: DependencyProvider
    reason: str = None


def inject(**dependencies):
    '''A decorator to indicate that a function requires dependencies:

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
        for k, v in deps.items():
            if isinstance(v, InjectionKey):
                yield k, v
            elif v is None:
                yield k, v
            else:
                yield k, InjectionKey(v, require_type=True)

    def init_from_bases(c, dependencies, autokwargs):
        for b in c.__bases__:
            if hasattr(b, "_injection_dependencies"):
                autokwargs -= b._injection_this_level
                dependencies.update(b._injection_dependencies)
                autokwargs |= b._injection_autokwargs

    def wrap(fn):
        if getattr(fn, '_injection_error', None):
            #Set _injection_error on decorators or other things that might be mistakenly targets of inject.
            raise TypeError(fn._injection_error)
        
        if (not hasattr(fn, '_injection_dependencies')) or (isinstance(
                fn, type) and '_injection_dependencies' not in fn.__dict__):
            fn._injection_dependencies = dict()
            fn._injection_this_level = set()
            fn._injection_autokwargs = set()
            if isinstance(fn, type):
                init_from_bases(fn, fn._injection_dependencies, fn._injection_autokwargs)

        for k, v in convert_to_key(dependencies):
            try:
                fn._injection_autokwargs.remove(k)
            except KeyError:
                pass
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

    :class:`InjectionKey`s with *_optional* set to NotPresent are not required.
    '''
    def wrap(cls):
        inject(**dependencies)(cls)
        cls._injection_autokwargs |= set(filter(
            lambda k: cls._injection_dependencies[k].optional is not NotPresent, cls._injection_this_level))
        return cls
    return wrap


def copy_and_inject(_wraps=None, **kwargs):
    "Like inject but makes a copy of the dependencies first; typically used when wrapping an injector"
    def wrap(fn):
        if hasattr(fn, '_injection_dependencies'):
            fn._injection_dependencies = fn._injection_dependencies.copy()
        return inject(**kwargs)(fn)
    if _wraps is not None:
        return wrap(_wraps)
    else:
        return wrap


Injector = inject(parent_injector=Injector)(Injector)


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
    except AttributeError:
        pass
    return p


def directly_has_dependencies(f):
    '''

    :return: True if *f* directly has injection dependencies applied.  Not true for an object of a class even if that class has dependencies.

    '''
    if not hasattr(f, '__dict__'):
        return False
    return '_injection_dependencies' in f.__dict__


class dependency_quote:

    '''
Indicate that a value (or dependency provider) should not be subject to injcoter resolution.  Used like::

        injector(func, override = dependency_quote(self))

    For example if *self* is an object that is not yet :meth:`~AsyncInjectable.async_ready` as part of bootstrapping.  Can be used both in a keyword argument to an injector call or in calls to :meth:`Injector.add_provider`.

'''

    __slots__ = ['value', '__transclusion_key__']

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f'dependency_quote({repr(self.value)})'

   #########################################
   # Asynchronous support:


@inject_autokwargs(
    injector=Injector)
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
            else:
                self._async_ready_state = ReadyState.RESOLVED
        else:  # has nontrivial async_resolve
            self._async_ready_state = ReadyState.NOT_READY

    async def async_ready(self):
        self._async_ready_state = ReadyState.READY
        return self

    async def async_resolve(self):
        '''Returns None or an object that should replace *self* in providing dependencies.'''
        return None

    async def async_become_ready(self, cycle_set=None, dependency_key=None):

        '''
        Interface point to request that a dependency be fully ready (:meth:`async_ready` is called exactly once).  This function manages tracking to make sure that async_ready is called once, and waits for that call if needed.
        This method also makes sure that dependencies registered with :func:`.inject` are made ready before :meth:`async_ready` is called.
        Subclasses should not override this method but instead should override :meth:`async_ready`.
        '''
        with AsyncBecomeReadyContext(self, dependency_key=dependency_key):
            if self._async_ready_state == ReadyState.NOT_READY:
                raise RuntimeError("Resolution should have already happened")
            elif self._async_ready_state == ReadyState.RESOLVED:
                self._ready_future = asyncio.ensure_future(_handle_async_deps(self, cycle_set))
                self._ready_future.set_name(f'Async dependencies for {self}')
                self._async_ready_state = ReadyState.READY_PENDING
                try:
                    return await self._ready_future
                except BaseException:
                    self._async_ready_state = ReadyState.RESOLVED
                    raise
                finally:
                    del self._ready_future
            elif self._async_ready_state == ReadyState.READY_PENDING:
                return await asyncio.shield(self._ready_future)
            else:
                return


@inject(loop=asyncio.AbstractEventLoop, injector=Injector)
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
        # For methods that injector has but we do not, then call the method on our
        # injector.  This is a lot like inheritance but does not make us a
        # subclass.
        for k in list(Injector.__dict__.keys()) + list(event.EventListener.__dict__.keys()):
            if not isinstance(getattr(Injector, k), types.FunctionType):
                continue

            if hasattr(self, k):
                continue
            setattr(self, k, getattr(self.injector, k))

    def claim(self, claimed_by=True):
        if self.injector.is_claimed:
            return type(self)(injector=self.injector.claim(claimed_by),
                              loop=self.loop)
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
        try:
            res = self._instantiate(
                cls, *args, **kwargs,
                _loop=self.loop,
                _placement=None,
                _interim_placement=None,
            )

            if isinstance(res, (asyncio.Future, collections.abc.Coroutine)):
                return await res
            else:
                return res
        except Exception as e:
            if filter_tracebacks:
                tb_utils.filter_chatty_modules(e, _chatty_modules, 1)
            raise

    async def get_instance_async(self, k):
        futures = []
        try:
            res = self.get_instance(k,
                                    loop=self.loop,
                                    futures=futures)
            if isinstance(res, (asyncio.Future, collections.abc.Coroutine)):
                return await res
            else:
                return res
        except Exception as e:
            if filter_tracebacks:
                tb_utils.filter_chatty_modules(e, _chatty_modules, 1)
            raise

    async def filter_instantiate_async(self, target, predicate,
                                       *, stop_at=None,
                                       ready=None):

        '''
        Like :meth:`filter_instantiate` except in an async context.  With *filter_instance* objects are not-ready by default.  With *filter_instance_async*, the ready state is default (true unless in the middle of instantiating something with ready false).

        '''
        results = []
        result_keys = []
        for k in self.filter(target, predicate, stop_at=stop_at):
            if ready is not None:
                k = InjectionKey(k, _ready=ready)
            results.append(self.get_instance_async(k))
            result_keys.append(k)
        results = await asyncio.gather(*results)
        zipped = zip(result_keys, results)
        return [z for z in zipped if z[1] is not None]

async def _handle_1_dep(val, cycle_set, dependency_key):
    with AsyncBecomeReadyContext(val, dependency_key) as context:
        try: await val.async_become_ready(cycle_set=cycle_set, dependency_key=dependency_key)
        except Exception as e:
            tb_utils.filter_before_here(e)
            failed_instantiation(context, e)
            raise InjectionFailed(context) from e


async def _handle_async_deps(obj, cycle_set):
    if cycle_set is None:
        cycle_set = set()
    futures = []
    try:
        repr = repr(obj)
    except Exception:
        repr = f"<Error in repr for {obj.__class__.__name__}"
    for attr, dep in obj.__class__._injection_dependencies.items():
        if dep is None:
            continue
        if dep.ready is False:
            continue
        val = getattr(obj, attr, None)
        if val is None:
            continue
        if is_obj_ready(val):
            continue
        if id(val) in cycle_set:
            import warnings
            warnings.warn(f'{obj}.async_become_ready: @inject({attr}={dep}) produced dependency cycle with {val}')
            continue
        cycle_set.add(id(val))
        future = asyncio.ensure_future(_handle_1_dep(val, cycle_set=cycle_set, dependency_key=dep))
        future.set_name(f'Dependency become ready for {dep} of {repr}')
        futures.append(future)

    gather = asyncio.gather(*futures)
    try: await gather
    except Exception as e:
        tb_utils.filter_chatty_modules(e, _chatty_modules, None)
        raise
    return await obj.async_ready()


# Injector cross reference support
class InjectorXrefMarkerMeta(type):

    def __repr__(self):
        return f'injector_xref({self.injectable_key}, {self.target_key})'

    def __hash__(self):
        try:return hash(self.injectable_key)+hash(self.target_key)
        except AttributeError:
            return hash(self.target_key)

    def __eq__(self, other):
        if not isinstance(other, InjectorXrefMarkerMeta): return NotImplemented
        if getattr(self, 'injectable_key', None) != getattr(other, 'injectable_key', None): return False
        if self.target_key != other.target_key: return False
        return True

class InjectorXrefMarker(AsyncInjectable, metaclass=InjectorXrefMarkerMeta):

    # A separate subclass is created for each injector_xref with one
    # class attributes: target_key.  If possible our __new__ entirely
    # resolves to target_key and simply returns that object.  If that
    # requires asynchronous action, then we actually instantiate
    # ourself and depend on async_resolv.  We receive an injectable
    # through dependency injection.

    def __new__(cls, injectable):
        if isinstance(injectable, Injector):
            injector = injectable
        else:
            injector = injectable.injector
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
        yield None  # to make it a generator

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

    :param injectable_key: The :class:`InjectionKey` of an :class:`Injectable` or an :class:`Injector` in which the target is actually looked up.  If *None*, then look up in the current injector.

    :param target_key: An :class:`InjectionKey` registered with the
    injector belonging to *injectable_key*.  It is important that
    *target_key* actually be provided by that injector.  In the common
    case where the parent of *injectable_key* eventually chains back
    to the injector in which this *injector_xref* is provided, a loop
    can happen otherwise.

    '''
    # If we return an AsyncInjectable that needs to be ready, our
    # caller will handle calling async_become_ready.  We want to
    # requesta not_ready object to avoid cycles.  If asynchronous is
    # called, we'll hold with an interim future providing the
    # dependency while InjectorXrefMarker's async_resolve runs.
    # That's necessary.  If we don't explicitly request _ready=False,
    # we will also hold with an interim future while the inner
    # async_become_ready is called.  In cases like AnsibleInventory
    # where someone asks for _ready=False while async_become_ready is
    # running, this creates a cycle.
    tkey = InjectionKey(target_key, _ready=False)
    ikey = injectable_key if injectable_key is not None else InjectionKey(Injector)

    @inject(injectable=ikey,
            injector=None)
    class instance(InjectorXrefMarker):
        target_key = tkey
        injectable_key = ikey
    return instance


async def shutdown_injector(injector, timeout=5):

    '''
    Close an injector and cancel running tasks


    This closes an injector, canceling any running tasks.  It waits up to *timeout* seconds for any canceled tasks to terminate.

'''
    canceled_futures = []
    injector.close(canceled_futures=canceled_futures)
    if canceled_futures:
        await asyncio.wait(canceled_futures, timeout=timeout)


def _call_close(obj, canceled_futures):
    if not hasattr(obj, 'close'):
        return
    sig = inspect.signature(obj.close)
    try:
        if 'canceled_futures' in sig.parameters:
            return obj.close(canceled_futures=canceled_futures)
        else:
            return obj.close()
    except TypeError:
        pass  # calling on not yet constructed class


def aspect_for(cls: typing.Type[Injectable],
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
            raise TypeError(f'{k} is already a dependency for {cls.__name__}.')
        inject(property=val)(cls)
        return val
    return wrapper


def is_obj_ready(obj):
    if isinstance(obj, AsyncInjectable):
        return obj._async_ready_state == ReadyState.READY
    return True


@contextlib.contextmanager
def injection_failed_unlogged():
    '''Typically InjectionFailed is logged at severity error.  Within this context, InjectionFailed errors are not logged.
    '''
    reset = log_injection_failed.set(False)
    try:
        yield
    finally:
        log_injection_failed.reset(reset)

@contextlib.contextmanager
def instantiation_not_ready(ready=False):
    '''
    In this context, calling an :class:`Injector`, or calling methods like :meth:`Injector.get_instance` will not bring objects to ``async_ready`` state unless the *ready* parameter is True.

    This can be overridden in the key of a dependency.  For example::

        @inject_autokwargs(bar=InjectionKey(Bar, _ready=True))
        class Foo(AsyncInjectable):

            async def async_ready(self): pass # do stuff in this method

    And then later::

        with instantiation_not_ready():
            foo = await ainjector(Foo)

    The *bar* dependency of *foo* will be brought to *async_ready* state.  However, *foo* itself will not be ready: *foo.async_ready()* will not be called.

    Do not confuse this function with :var:`instantitate_to_ready`.  This is a context manager to manipulate the desired ready state.  *instantiate_to_ready* is an internal context variable that tracks desired ready state.
    Calling ``instantiation_not_ready(ready=True)`` may be a bit counter-intuitive; it will reverse the effect of calling ``instantiation_not_ready()`` and turn on default ready state.
    
    '''
    reset = instantiate_to_ready.set(ready)
    try:
        yield
    finally:
        instantiate_to_ready.reset(reset)


async def resolve_deferred(ainjector, item, args:dict):
    '''
    Often it is desirable to have a data structure that has some elements dynamically calculated.  For example looking up something in the environment of an injector, or calling some function that takes dependencies from an injector.
    Examples of where this is used include :class:`~carthage.network.NetworkConfig`.

    This function traverses a data structure starting at an atomic element, list, or dict.  It returns a copy where:

    *  :class:`InjectionKey` are replaced with :meth:`AsyncInjector.get_instance_async` in the context of the current injector

    * A *callable* is called in the context of the current injector.  If the callable has a signature, and if items in *args* match keys in the signature, the value from *args* is plugged into the call.  An injected dependency could be used instead, but in some cases the performance of setting up an injector just for one parameter value doesn't make sense.
    

    * dicts, lists, and tuples are recursed, although tuples become lists.

    * Other items are not modified.

    This function does not deal with recursive data structures.
    '''
    # This effectively uses cps internally.  The result argument to the inner functions is a callback that takes the result and puts it in the right place.
    def handle_list(l, result):
        def place_list(idx):
            def place(elt):
                out[idx] = elt
            return place
        out = [None]*len(l)
        for i, elt in enumerate(l):
            handle(elt, place_list(i))
        result(out)
    def handle_dict(d, result):
        def place_dict(k):
            def place(elt):
                out[k] = elt
            return place
        out = {k:None for k in d} # preserve order
        for k,v in d.items():
            handle(v, place_dict(k))
        result(out)
    def handle_injection(k, result):
        fut = asyncio.ensure_future(ainjector.get_instance_async(k))
        def done(f):
            result(f.result())
        fut.add_done_callback(done)
        futures.append(fut)
    def handle_callable(c, result):
        try:
            signature = inspect.signature(c)
            keys = signature.parameters.keys()
        except Exception: keys = set()
        in_args = {}
        for k in set(keys)& args_keys:
            in_args[k] = args[k]
        fut = asyncio.ensure_future(ainjector(c, **in_args))
        def done(f):
            result(f.result())
        fut.add_done_callback(done)
        futures.append(fut)
    def handle(elt, result):
        if isinstance(elt, dict):
            handle_dict(elt, result)
        elif isinstance(elt, (list,tuple)):
            handle_list(elt, result)
        elif isinstance(elt, InjectionKey):
            handle_injection(elt, result)
        elif callable(elt):
            handle_callable(elt, result)
        else:result(elt)
    futures = []
    args_keys = frozenset(args.keys())
    def base_result(elt):
        nonlocal to_return
        to_return = elt
    to_return = None
    handle(item, base_result)
    if futures: await asyncio.gather(*futures)
    return to_return


__all__ = [
    'AsyncInjectable', 'AsyncInjector', 'AsyncRequired',
    'DependencyProvider',
    'ExistingProvider', 'Injectable', 'InjectionFailed',
    'InjectionKey', 'Injector', 'InstantiationContext', 'aspect_for',
    'NotPresent',
    'dependency_quote', 'inject',
    'inject_autokwargs', 'injector_xref',
    'partial_with_dependencies', 'shutdown_injector',
    'resolve_deferred',
    'injection_failed_unlogged', 'instantiation_not_ready']
