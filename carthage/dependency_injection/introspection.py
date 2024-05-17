# Copyright (C)  2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations

import contextvars
import dataclasses
import traceback

from ..utils import memoproperty

_current_instantiation = contextvars.ContextVar('current_instantiation', default=None)

__all__ = []


class GetItemSet(set):

    def __getitem__(self, idx):
        self_iter = iter(self)
        result = None
        while idx >= 0:
            result = next(self_iter)
            idx -= 1
        return result


instantiation_roots = GetItemSet()

__all__ += ['instantiation_roots']


class BaseInstantiationContext:

    def __init__(self, injector):
        self.injector = injector
        self.dependencies_waiting = {}
        self.parent = None
        self._done = False

    def dependency_progress(self, key, context):
        '''Indicate that this instantiation has a dependency on *key*, currently in progress, with state tracked by *context*.'''
        self.dependencies_waiting.setdefault(key, context)

    def dependency_final(self, key, context):
        try:
            del self.dependencies_waiting[key]
        except KeyError:
            pass

    def __enter__(self):
        self.parent = _current_instantiation.get()
        if not self.parent:
            instantiation_roots.add(self)
        self.reset_token = _current_instantiation.set(self)
        return self

    def __exit__(self, *args):
        _current_instantiation.reset(self.reset_token)
        return False

    def done(self):
        '''Indicate that the instantiation has completed'''
        assert not self._done
        self._done = True
        if not self.parent:
            instantiation_roots.remove(self)

    def __str__(self):
        res = self.description
        if self.parent:
            res = f'{str(self.parent)} -> {res}'
        return res

    def __repr__(self):
        return f'{self.__class__.__name__}<{str(self)}>'


__all__ += ['BaseInstantiationContext']


@dataclasses.dataclass
class InjectedDependencyInspector:

    '''
    Using :func:`~carthage.dependency_injection.inject`, a dependency can be injected into a function or class.  Various APIs such as :meth:`~carthage.dependency_injection.introspection.get_dependencies_for` or :func:`get_dependencies_for` will allow introspection of these dependencies.  Those methods iterate over *INjectedDependencyInspector*s to give information on each dependency.
    '''

    #: The :class:`Injector` that provides the dependency. *None* if the dependency is unresolved.
    injector: typing.Optional[Injector]

    provider: DependencyProvider

    key: InjectionKey  # : What dependency is injected

    @property
    def is_provided(self):
        '''True if the dependency is provided; False if there is no injector in the chain providing this dependency.'''
        return self.provider is not None

    @property
    def is_final(self):
        '''True if the dependency has been fully instantiated.  If true, :meth:`get_value` will never raise *AsyncRequired*.
'''
        return self.is_provided and not self.provider.is_factory

    def all_keys(self):
        '''Iterate over all keys that provide this dependency in the given injector.
'''
        if not self.is_provided:
            yield self.key
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

    def get_value_no_instantiate(self):
        '''Return the current value without instantiating; I.E. possibly whatever is passed into add_provider.
        '''
        return self.provider.provider

    async def get_value_async(self, ready=None):
        '''Like :func:`get_value` but asynchronous.
        '''
        from .base import AsyncInjector, InjectionKey
        ainjector = AsyncInjector(self.injector)
        key = self.key
        if ready is not None:
            key = InjectionKey(key, _ready=ready)
        return await ainjector.get_instance_async(key)

    @property
    def instantiation_contexts(self):
        '''Return any ongoing instantiations referencing this dependency.  Typically contexts are very short lived if :meth:`is_final` is true.  However if a dependency is itself waiting for dependencies, the contexts can be used to chain those dependencies.
'''
        if not self.provider:
            return frozenset()
        return frozenset(self.provider.instantiation_contexts)

    def all_waiting_dependencies(self):
        result = {}
        for c in self.instantiation_contexts:
            result.update(c.dependencies_waiting)
        return result


    @property
    def provider_id(self):
        "If two inspectors have the same provider_id, they are guaranteed to refer to the same value.  It is possible that inspectors with different provider_id may refer to the same value.  Once is_final returns true, value_id will be stable."
        if not self.is_provided: return None
        return id(self.provider)
    
        

__all__ += ['InjectedDependencyInspector']


class InstantiationContext(BaseInstantiationContext, InjectedDependencyInspector):

    '''
    Represents the instantiation of an :class:`InjectionKey` in the scope of a :class:`Injector`.
    '''

    def __init__(self, injector: injector, triggering_injector: Injector, key: InjectionKey,
                 provider: DependencyProvider,
                 ready: bool):
        BaseInstantiationContext.__init__(self, injector)
        InjectedDependencyInspector.__init__(self, injector=injector, key=key, provider=provider)
        self.triggering_injector = triggering_injector
        self.ready = ready

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    def __enter__(self):
        for ctx in self.provider.instantiation_contexts:
            # We only care about the first one
            assert not self.dependencies_waiting
            self.dependencies_waiting = ctx.dependencies_waiting
            break
        self.provider.instantiation_contexts.add(self)
        res =  super().__enter__()
        loop_detect = set()
        cur = self
        while cur:
            try:
                loop_tuple = cur.key, cur.injector, cur.key.ready is False
                if loop_tuple in loop_detect:
                    raise RuntimeError('injection loop detected: '+repr(self))
                loop_detect.add(loop_tuple)
            except AttributeError: pass
            cur = cur.parent
        return res

    @property
    def description(self):
        desc = f'Instantiating {self.key} using {self.injector}'
        if self.injector is not self.triggering_injector:
            desc += f' for {self.triggering_injector}'
        return desc

    def done(self):
        super().done()
        self.provider.instantiation_contexts.remove(self)

    def progress(self):
        for ctx in self.provider.instantiation_contexts:
            parent = ctx.parent
            if not parent:
                continue
            parent.dependency_progress(self.key, self)
        self.injector.emit_event(self.key, "dependency_progress",
                                 self,
                                 adl_keys=self.provider.keys | {base.InjectionKey(base.Injector)})

    def final(self):
        from .base import is_obj_ready
        obj_ready = is_obj_ready(self.provider.provider)
        for ctx in self.provider.instantiation_contexts:
            parent = ctx.parent
            if parent is None:
                continue
            if ctx.ready and not obj_ready:
                continue
            # We try to avoid calling dependency_final when the object
            # is not ready yet.  However since instantiations can
            # share the dependencies_waiting dict, there are cases
            # where dependency_finaly will be called anyway.  It's
            # important that when _handle_async handles a future, it
            # calls progress soon to recover from that.
            parent.dependency_final(self.key, self)
        if obj_ready or not self.ready:
            self.injector.emit_event(self.key,
                                     "dependency_final", self,
                                     adl_keys=self.provider.keys | {base.InjectionKey(base.Injector)})

    def get_dependencies(self):
        return get_dependencies_for(self.provider.provider, self.injector)


__all__ += ['InstantiationContext']


def current_instantiation():
    return _current_instantiation.get()


__all__ += ['current_instantiation']


class AsyncBecomeReadyContext(BaseInstantiationContext):

    def __init__(self, obj, dependency_key):
        super().__init__(obj.injector)
        if dependency_key is not None:
            # Setting self.key to None may make the instantiation
            # failed tracking mechanisms sad; they may expect that key
            # is always an InjectionKey if present, so check with hasattr before accessing key.
            self.key = dependency_key
        self.obj = obj
        self.entered = False

    @property
    def description(self):
        return f'bringing {self.obj} to ready'

    def __enter__(self):
        # If the parent context is an instantiation context for
        # ourself (async_become_ready as part of get_instance_async),
        # or the parent is already an AsyncBecomeReadyContext for
        # ourself (_handle_1_dep), we do not need an extra level of
        # context.
        parent = _current_instantiation.get()
        if isinstance(parent, InstantiationContext) and parent.provider.provider is self.obj:
            return parent
        elif isinstance(parent,AsyncBecomeReadyContext) and self.obj is parent.obj:
            return parent
        self.entered = True
        res =  super().__enter__()
        if self.parent:
            self.parent.dependency_progress(self.dependency_key, self)
        return res

    def __exit__(self, *args):
        if self.entered:
            super().__exit__(*args)
            self.done()
        return False

    def get_value_no_instantiate(self):
        return self.obj

    def get_dependencies(self):
        return get_dependencies_for(self.obj, self.obj.injector)

    def done(self):
        super().done()
        if self.entered and self.parent:
            self.parent.dependency_final(self.dependency_key, self)

    @memoproperty
    def dependency_key(self):
        if hasattr(self, 'key'):
            return self.key
        try:
            return str(self.obj)
        except Exception:
            return repr(self.obj)
    

__all__ += ['AsyncBecomeReadyContext']


def get_dependencies_for(obj, injector):
    if not hasattr(obj, '_injection_dependencies'):
        return
    for injection_key in obj._injection_dependencies.values():
        try:
            dp, satisfy_against = injector._get_parent(injection_key)
            yield InjectedDependencyInspector(
                injector=satisfy_against,
                key=injection_key,
                provider=dp)
        except KeyError:
            yield InjectedDependencyInspector(
                injector=injector,
                key=injection_key,
                provider=None)


__all__ += ['get_dependencies_for']


@dataclasses.dataclass
class FailedInstantiation:

    exception: Exception
    dependency: tuple[base.InjectionKey] = dataclasses.field(default_factory=lambda: tuple())

    def __repr__(self):
        return \
            f"<Failed  Instantiation (dependency Path: {self.dependency}\n"\
            +"\n".join(traceback.format_exception(self.exception))+">"
        
def failed_instantiation(context, exception):
    injector = context.injector
    key = context.key
    dependency = tuple()
    if isinstance(exception, base.InjectionFailed):
        dependency = exception.dependency_path
        exception = exception.__context__
    failure = FailedInstantiation(exception=exception, dependency=dependency)
    all_instantiation_failures[id(injector), key] = failure
    if len(dependency) == 0:
        failed_instantiation_leaves[id(injector), key] = failure
    injector.emit_event(
        key, "dependency_instantiation_failed",
        failure,
        context=context,
        adl_keys={base.InjectionKey(base.Injector)})
    

__all__ += ['failed_instantiation']

all_instantiation_failures: dict[tuple, FailedInstantiation] = {}
failed_instantiation_leaves: dict[tuple, FailedInstantiation] = {}

__all__ += ['all_instantiation_failures', 'failed_instantiation_leaves']

def calculate_reverse_dependencies(obj: object, /, injector,
                                   *, reverse_dependencies: dict[object,set[object]],
                                   filter=lambda o:True):
    def visit(dependencies,val, cycle):
        reverse_dependencies.setdefault(val, set())
        for dependency in dependencies:
            try:
                inner_val = dependency.get_value(ready=False)
            except KeyError: continue
            if not filter(inner_val): continue
            if inner_val in cycle: continue
            found_dependency = True
            if inner_val not in reverse_dependencies:
                reverse_dependencies.setdefault(inner_val, set())
            reverse_dependencies[inner_val] |= {val}
            visit(get_dependencies_for(inner_val, dependency.injector), inner_val, cycle=cycle|{inner_val})
            
    visit(get_dependencies_for(obj, injector),
          obj, {obj})

__all__ += ['calculate_reverse_dependencies']

def instantiation_leaves():
    '''Start from :data:`instantiation_roots` and chase down all the
    dependencies.  Return all contexts that do not have any
    dependencies waiting that have not already been seen.
    '''
    to_consider = list(instantiation_roots)
    ids_seen = set()
    leaves = set()
    while to_consider:
        to_consider_next = set()
        for context in to_consider:
            has_dependencies = False
            ids_seen.add(id(context))
            for dependency in context.dependencies_waiting.values():
                if id(dependency) in ids_seen:
                    continue
                to_consider_next.add(dependency)
                has_dependencies = True
            if not has_dependencies:
                leaves.add(context)
        to_consider = to_consider_next
    return leaves

__all__ += ['instantiation_leaves']

from . import base
