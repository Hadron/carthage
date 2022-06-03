# Copyright (C)  2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations


import contextvars

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
        for parent in self.provider.instantiation_contexts:
            parent.dependency_progress(self.key, self)

    def final(self):
        for parent in self.provider.instantiation_contexts:
            parent.dependency_final(self.key, self)

__all__ += ['InstantiationContext']

def current_instantiation():
    return _current_instantiation.get()

__all__ += ['current_instantiation']

class AsyncBecomeReadyContext(BaseInstantiationContext):

    def __init__(self, obj):
        super().__init__(obj.injector)
        self.obj = obj
        self.entered = False

    def __enter__(self):
        parent = _current_instantiation.get()
        if isinstance(parent, InstantiationContext) and  parent.provider.provider is self.obj:
            return parent
        self.entered = True
        return super().__enter__()

    def __exit__(self, *args):
        if self.entered:
            super().__exit__(*args)
        return False

__all__ += ['AsyncBecomeReadyContext']

