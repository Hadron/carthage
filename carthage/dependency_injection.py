# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import weakref
import collections.abc
import asyncio
import logging
import types

logger = logging.getLogger('carthage.dependency_injection')

class Injectable:
    def __init__(self, *args, **kwargs):
        super().__init__()

    @classmethod
    def supplementary_injection_keys(cls, k):
        for c in cls.__mro__:
            if c is Injectable: continue
            if issubclass(c,Injectable) and c != k.target:
                yield InjectionKey(c)

    @classmethod
    def satisfies_injection_key(cls, k):
        if k is InjectionKey(cls): return True
        if isinstance(k.target, (str, tuple)): return True
        return  k in cls.supplementary_injection_keys(k)

class DependencyProvider:
    __slots__ = ('provider',
                 'allow_multiple',
                 )

    def __init__(self, provider, allow_multiple = False):
        self.provider = provider
        self.allow_multiple = allow_multiple

    def __repr__(self):
        return "<DependencyProvider allow_multiple={}: {}>".format(
self.allow_multiple, repr(self.provider))

    @property
    def is_factory(self):
        return (isinstance(self.provider, type) and issubclass(self.provider, Injectable)) \
            or asyncio.iscoroutinefunction(self.provider) \
            or asyncio.isfuture(self.provider)

    def record_instantiation(self, instance, k, satisfy_against):
        dp = satisfy_against._providers.setdefault(k, DependencyProvider(instance, self.allow_multiple))
        assert dp.is_factory or dp.provider is instance
        dp.provider = instance
        return dp
        
    


# Note that after @inject is defined, this class is redecorated to take parent_injector as a dependency so that
#    injector = sub_injector(Injector)
# works
class Injector(Injectable):

    def __init__(self, *providers,
                 parent_injector = None):
        self._providers = {}
        if parent_injector is None and len(providers) > 0:
            if isinstance(providers[0], Injector):
                parent_injector = providers[0]
                providers = providers[1:]

        self.parent_injector = parent_injector
        for p in providers:
            self.add_provider(p)
        self.add_provider(self) #Make sure we can inject an Injector
        self.add_provider(InjectionKey(AsyncInjector ), AsyncInjector, allow_multiple = True)

    def copy_if_owned(self):
        # currently always copies
        return type(self)(self)

    def claim(self):
        "Take ownership of the injector"
# Currently a stub
        return self

    def add_provider(self, k, p = None, *,
                     allow_multiple = False):
        '''Either called as add_provider(provider) or
        add_provider(injection_key, provider).  In the first form, a key is
        automatically constructed.
'''
        if p is None:
            p,k = k,p #swap; we construct the key later

        if k is None:
            if isinstance(p, DependencyProvider): raise NotImplementedError
            k = InjectionKey(p if isinstance(p,type) else p.__class__)
        if not isinstance(p, DependencyProvider):
            p = DependencyProvider(p, allow_multiple = allow_multiple)
        assert isinstance(k,InjectionKey)
        if k in self:
            if p is self._get(k): return k
            existing_provider = self._get(k)
            if existing_provider.is_factory:
                pass # provider permitted
            else: raise ExistingProvider(k)
        self._providers[k] = p
        for k2 in k.supplementary_injection_keys(p.provider):
            if k2 not in self:
                self._providers[k2] = p
        return k

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

    def __contains__(self, k):
        return k in self._providers
    
    def __call__(self, cls, *args, **kwargs):
        '''Construct an instance of cls using the providers in this injector.
        Instantiate providers as needed.  In general a sub-injector is not
        constructed.  However if any keyword arguments pased in specify a
        dependency, then construct an injector for that.  Keyword arguments
        and arguments are passed to the class to construct the object.  If
        keyword arguments do specify a dep.dependency, they must satisfy the
        InjectionKey involved.
'''
        try:
            dks = set(cls._injection_dependencies.keys())
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
                    sub_injector.add_provider(dependency, provider)
            for k, d in (cls._injection_dependencies.items()) if dks else []:
                kwargs[k] = injector.get_instance(d)
            return cls(*args, **kwargs)
        finally:
            #Perhaps some day we need to clean up something about the sub_injector
            pass


    def get_instance(self, k,
                     futures_instantiate = None):
        logger.debug("Looking up provider for {}".format(k))
        def resolve_future(injector,k):
            def done(future):
                try: provider.record_instantiation(future.result(), k, injector)
                except: pass #Will be handled in our caller who also attaches a done callback
            return done
                

        def no_futures_instantiate(injector, p):
            return injector(p)

        if not isinstance(k, InjectionKey):
            k = InjectionKey(k)
        if futures_instantiate: instantiate = futures_instantiate
        else: instantiate = no_futures_instantiate
        try:
            provider, satisfy_against = self._get_parent(k)
        except KeyError:
            if k.optional: return None
            raise KeyError("No dependency for {}".format(k)) from None
        if provider.is_factory:
            instance = instantiate(satisfy_against,  provider.provider)
            if self._is_async(instance):
                if not futures_instantiate:
                    raise UnsatisfactoryDependency("{} has an asynchronous provider injected into a non-asynchronous context".format(k))
                future = self._handle_async(instance)
                future.add_done_callback(resolve_future( satisfy_against, k))
                provider.record_instantiation(future, k, satisfy_against) 
                return future
            else:
                provider = provider.record_instantiation(instance, k, satisfy_against)
        return provider.provider

    
    def _is_async(self, p):
        if isinstance(p, (collections.abc.Coroutine, AsyncInjectable,
                          asyncio.Future)):
            return True
        return False
    

    def _handle_async(self, p):
        if not hasattr(self, 'loop'):
            self.loop = self.get_instance(InjectionKey(asyncio.AbstractEventLoop))
        if isinstance(p, collections.abc.Coroutine):
            return asyncio.ensure_future(p, loop = self.loop)
        if isinstance(p, AsyncInjectable):
            return asyncio.ensure_future(p.async_ready(), loop = self.loop)
        if isinstance(p, asyncio.Future): return p
        raise RuntimeError('_is_async returned True when _handle_async cannot handle')


class InjectionKey:


    _target_injection_keys = weakref.WeakKeyDictionary()

    def __new__(cls, target_, require_type = False, optional = False, **constraints):
        assert (cls is InjectionKey) or constraints, "You cannot subclass InjectionKey with empty constraints"
        if require_type and not isinstance(target_, type):
            raise TypeError('Only types can be used as implicit injection keys; if this is intended then construct the injection key explicitly')
        if (not constraints) and (not optional):
            if  target_ in cls._target_injection_keys:
                return cls._target_injection_keys[target_]
        self =super().__new__(cls)
        self.__dict__['constraints'] = dict(constraints)
        self.__dict__['target'] = target_
        self.__dict__['optional'] = optional
        if (not optional) and len(constraints) == 0 and not isinstance(target_, (str, int, float)):
            cls._target_injection_keys[target_] = self
        return self


    def __getattr__(self,k):
        if k in self.__dict__['constraints']: return self.__dict__['constraints'][k]
        return self.__dict__[k]

    def __repr__(self):
        r = "InjectionKey({}".format(
            self.target.__name__ if isinstance(self.target, type) else repr(self.target))
        for k,v in self.constraints.items():
            r += ",\n    {} = {}".format(
                repr(k), repr(v))
        return r+")"

    def __setattr__(self, k, v):
        raise TypeError('InjectionKeys are immutible')


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
                

def inject(**dependencies):
    '''A dhecorator to indicate that a function requires dependencies:

    @inject(injector = Injector,
        router = InjectionKey(SiteRouter, site ='cambridge'))
    def testfn(injector, router): pass

    Can be applied to classes or functions
    '''
    def convert_to_key(deps):
        for k,v in deps.items():
            if isinstance(v, InjectionKey):
                yield k,v
            else: yield k, InjectionKey(v, require_type = True)
    def wrap(fn):
        if (not hasattr(fn, '_injection_dependencies')) or (isinstance(fn, type) and '_injection_dependencies' not in fn.__dict__):
            fn._injection_dependencies = dict()
        fn._injection_dependencies.update(convert_to_key(dependencies))
        return fn
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
    
   #########################################
   # Asynchronous support:

class AsyncInjectable(Injectable):

    async def async_ready(self):
        return self


@inject(loop = asyncio.AbstractEventLoop, injector = Injector)
class AsyncInjector(Injectable):

    '''An asynchronous injector.  AsyncInjector is not a subclass of
    Injector because AsyncInjector's call function is a coroutine and
    so it has an incompatible interface.  In other ways the classes
    should behave the same.
'''

    def __init__(self, injector, loop):
        self.injector = type(injector)(injector) # create our own sub injector
        self.injector.add_provider(self)
        self.loop = loop
        # For methods that injector has but we do not, then call the method on our injector.  This is a lot like inheritance but does not make us a subclass.
        for k in Injector.__dict__.keys():
            if  not isinstance(getattr(Injector, k), types.FunctionType):
                continue

            if hasattr(self, k): continue
            setattr(self, k, getattr(self.injector, k))

    def _instantiate_future(self, injector, provider, *args, **kwargs):
        #__call__ handles overrides and anything in there is already satisfactory.
        def handle_future(k):
            def callback(fut):
                kwargs[k] = fut.result() # may raise
            return callback
        futures = []
        try: dependencies = provider._injection_dependencies
        except AttributeError: dependencies = {}
        for k,d in dependencies.items():
            if k in kwargs: continue
            p = injector.get_instance(d,
                                      futures_instantiate = self._instantiate_future)
            if isinstance(p, asyncio.Future):
                p.add_done_callback(handle_future(k))
                futures.append(p)
            else: kwargs[k] = p
        if futures:
            constructive_future = asyncio.ensure_future(
                self._instantiate_coro(
                    futures, provider, args, kwargs),
                loop = self.loop)
            return constructive_future
        else:
            try:
                res =  provider(*args, **kwargs)
            except TypeError as e:
                raise TypeError("Error constructing {}".format(provider)) from e
            if self._is_async(res):
                res = self._handle_async(res)
            return res

    async def _instantiate_coro(self, futures, provider, args, kwargs):
        await asyncio.gather(*futures)
        # That will raise if there are errors with any of the
        # constructions done callbacks on the futures have inserted
        # them into the kwargs dict we got as a parameter
        res =  provider(*args, **kwargs)
        if self._is_async(res):
            future = self._handle_async(res)
            res = await future
        return res

    async def __call__(self, cls, *args, **kwargs):
        '''Coroutine to Construct an instance of cls using the providers in this injector.
        Instantiate providers as needed.  In general a sub-injector is not
        constructed.  However if any keyword arguments pased in specify a
        dependency, then construct an injector for that.  Keyword arguments
        and arguments are passed to the class to construct the object.  If
        keyword arguments do specify a dep.dependency, they must satisfy the
        InjectionKey involved.
'''
        try:
            dks = set(cls._injection_dependencies.keys())
        except AttributeError: dks = set()
        injector = self.injector # or sub_injector if created
        sub_injector = None
        kwarg_dependencies = set(kwargs.keys()) & dks
        try: #clean up sub_injector
            if kwarg_dependencies:
                sub_injector = (type(self.injector))(self.injector)
                injector = sub_injector
                for k in kwarg_dependencies:
                    provider = kwargs.pop(k)
                    dependency = cls._injection_dependencies[k]
                    if isinstance(provider,Injectable) and not provider.satisfies_injection_key(dependency):
                        raise UnsatisfactoryDependency(dependency, provider)
                    sub_injector.add_provider(dependency, provider)
            res = self._instantiate_future(injector, cls, *args, **kwargs)
            if isinstance(res, (asyncio.Future, collections.abc.Coroutine)):
                return await self._handle_async(res)
            else: return res
        finally:
            pass # possibly clean up sub_injector some day

    async def get_instance_async(self, k):
        res = self.get_instance(k, futures_instantiate = self._instantiate_future)
        if isinstance(res, (asyncio.Future, collections.abc.Coroutine)):
            return await self._handle_async(res)
        else: return res

