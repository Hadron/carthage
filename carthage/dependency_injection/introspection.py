# Copyright (C)  2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations

import contextvars
import dataclasses

_current_instantiation = contextvars.ContextVar('current_instantiation', default=None)

__all__ = []

instantiation_roots = set()

__all__ += ['instantiation_roots']

class BaseInstantiationContext:

    def __init__(self, injector):
        self.injector = injector
        self.dependencies_waiting = {}
        self.parent = None
        self._done = False

    def dependency_progress(self, key, context):
        '''Indicate that this instantiation has a dependency on *key*, currently in progress, with state tracked by *context*.'''
        self.dependencies_waiting[key] = context

    def dependency_final(self, key, context):
        try:
            del self.dependencies_waiting[key]
        except KeyError: pass

    def __enter__(self):
        self.parent = _current_instantiation.get()
        if not self.parent: instantiation_roots.add(self)
        self.reset_token = _current_instantiation.set(self)
        return self

    def __exit__(self, *args):
        _current_instantiation.reset(self.reset_token)
        return False

    def done(self):
        '''Indicate that the instantiation has completed'''
        assert not self._done
        self._done = True
        if not self.parent: instantiation_roots.remove(self)

    def __str__(self):
        res = self.description
        if self.parent: res = f'{str(self.parent)} -> {res}'
        return res

    def __repr__(self):
        return f'{self.__class__.__name__}<{str(self)}>'

__all__ += ['BaseInstantiationContext']

class InstantiationContext(BaseInstantiationContext):

    '''
    Represents the instantiation of an :class:`InjectionKey` in the scope of a :class:`Injector`.
    '''


    def __init__(self, injector:injector, satisfy_against:Injector, key:InjectionKey,
                 provider:DependencyProvider):
        super().__init__(injector)
        self.satisfy_against = satisfy_against
        self.key = key
        self.provider = provider

    def __enter__(self):
        self.provider.instantiation_contexts.add(self)
        return super().__enter__()

    def __exit__(self, *args):
        self.provider.instantiation_contexts.remove(self)
        return super().__exit__(*args)

    @property
    def description(self):
        desc = f'Instantiating {self.key} using {self.satisfy_against}'
        if self.satisfy_against is not self.injector:
            desc += f' for {self.injector}'
        return desc


    def progress(self):
        for ctx in self.provider.instantiation_contexts:
            parent = ctx.parent
            if parent is None: continue
            parent.dependency_progress(self.key, self)

    def final(self):
        for parent in self.provider.instantiation_contexts:
            parent.dependency_final(self.key, self)

    def get_dependencies(self):
        return get_dependencies_for(self.provider.provider, self.injector)
    
__all__ += ['InstantiationContext']

def current_instantiation():
    return _current_instantiation.get()

__all__ += ['current_instantiation']

class AsyncBecomeReadyContext(BaseInstantiationContext):

    def __init__(self, obj):
        super().__init__(obj.injector)
        self.obj = obj
        self.entered = False

    @property
    def description(self):
        return f'{self.obj}.async_become_ready()'
    
    def __enter__(self):
        parent = _current_instantiation.get()
        if isinstance(parent, InstantiationContext) and  parent.provider.provider is self.obj:
            return parent
        self.entered = True
        return super().__enter__()

    def __exit__(self, *args):
        if self.entered:
            super().__exit__(*args)
            self.done()
        return False

    def get_dependencies(self):
        return get_dependencies_for(self.obj, self.obj.injector)
    
__all__ += ['AsyncBecomeReadyContext']

@dataclasses.dataclass
class InjectedDependencyInspector:

    '''
    Using :func:`~carthage.dependency_injection.inject`, a dependency can be injected into a function or class.  Various APIs such as :meth:`~carthage.dependency_injection.Injector.get_dependencies_for` or :func:`get_dependencies_for` will allow introspection of these dependencies.  Those methods iterate over *INjectedDependencyInspector*s to give information on each dependency.
    '''

    #: The :class:`Injector` that provides the dependency. *None* if the dependency is unresolved.
    injector:typing.Optional[Injector]

    provider: DependencyProvider

    key: InjectionKey #: What dependency is injected

    @property
    def is_provided(self):
        '''True if the dependency is provided; False if there is no injector in the chain providing this dependency.'''
        return self.provider is None
    
    @property
    def is_final(self):
        '''True if the dependency has been fully instantiated.  If true, :meth:`get_value` will never raise *AsyncRequired*.
'''
        return self.is_provided and not self.provider.is_factory


    def all_keys(self):
        '''Iterate over all keys that provide this dependency in the given injector.
'''
        if not self.is_provided: yield self.key
        else:
            yield from self.provider.keys

    def get_value(self, ready=None):
        '''Returns the value provided for the dependency.  May raise *KeyError* if not provided.
'''
        from .base import InjectionKey
        key = self.key
        if ready is not None:
            key = InjectionKey(key, _ready=ready)
        return self.injector.get_instance(key)

    async def get_value_async(self, ready=None):
        '''Like :func:`get_value` but asynchronous.
        '''
        from .base import AsyncInjector, InjectionKey
        ainjector = AsyncInjector(self.injector)
        key = self.key
        if ready is not None: key = InjectionKey(key, _ready=ready)
        return await ainjector.get_instance_async(key)

    @property
    def instantiation_contexts(self):
        '''Return any ongoing instantiations referencing this dependency.  Typically contexts are very short lived if :meth:`is_final` is true.  However if a dependency is itself waiting for dependencies, the contexts can be used to chain those dependencies.
'''
        if not self.provider: return frozenset()
        return frozenset(self.provider.instantiation_contexts)

    def all_waiting_dependencies(self):
        result = {}
        for c in self.instantiation_contexts:
            result.update(c.dependencies_waiting)
        return result
    
__all__ += ['InjectedDependencyInspector']

def get_dependencies_for(obj, injector):
    if not hasattr(obj, '_injection_dependencies'): return
    for injection_key in obj._injection_dependencies.values():
        try:
            dp, satisfy_against  = injector._get_parent(injection_key)
            yield InjectedDependencyInspector(
                injector=satisfy_against,
                key=injection_key,
                provider=dp)
        except KeyError:
            yield InjectedDependencyInspector(
                injector=None,
                key=injection_key,
                provider=None)
            
__all__ += ['get_dependencies_for']
