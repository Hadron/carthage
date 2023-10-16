# Copyright (C)  2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import asyncio
import dataclasses
import enum
from pathlib import Path
import random
import traceback
import uuid
import yaml

from entanglement.interface import *
from entanglement.memory import *
from entanglement.filter import *
from entanglement.types import register_type, register_enum


from ..dependency_injection import InjectionKey, Injector, is_obj_ready, get_dependencies_for
from ..setup_tasks import SetupTaskMixin, _iso_time

__all__ = []

def encode_injection_key(k):
    # Note that encoding and decoding an InjectionKey is not an identity.  In particular, the type will become a string.
    # This may or may not be reasonable depending on how we end up using this code.
    target = k.target
    if isinstance(target, type):
        target = target.__name__
    return [target, k.constraints]

def decode_injection_key(v):
    return InjectionKey(v[0],**v[1])
register_type(InjectionKey, encode_injection_key, decode_injection_key)

class CarthageDestination(FilteredSyncDestination):

    filter_should_listen_returns_true = True
    
    def __init__(self):
        super().__init__(dest_hash=random.randbytes(32),
                         name='websocket to ip address should go here')
        self.add_filter(Filter(lambda o: True, registry=carthage_registry))
        
class CarthageRegistry(SyncStoreRegistry):

    def associate_with_manager(self, manager):
        self.manager = manager # This should be weak according to docs
        super().associate_with_manager(manager)
        
    def injector_id(self, injector):
        return id(injector)
    
    def on_add_provider(self, scope,  other_keys, target_key, inspector,
                        **kwargs):
        injector_id = self.injector_id(scope)
        injector_info = self.get_or_create(InjectorInfo, injector_id)
        if scope.parent_injector:
            injector_info.parent_id = id(scope.parent_injector)
        try: injector_info.description = repr(scope)
        except Exception: pass
        self.store_synchronize(injector_info)
        for k in other_keys | {target_key}:
            o = ProvidedDependency(injector_id=injector_id, key=k, provider_id=inspector.provider_id)
            self.store_synchronize(o)
        provider_info = self.get_or_create(ProviderInfo, inspector.provider_id)
        try: provider_info.value_repr = repr(inspector.get_value_no_instantiate())
        except Exception:
            provider_info.value_repr = None
        self.store_synchronize(provider_info)
        for dependency in get_dependencies_for(inspector.get_value_no_instantiate(), inspector.injector):
            sd = StaticDependency(
                provider_id=inspector.provider_id,
                key=dependency.key,
                provided_by=dependency.provider_id)
            self.store_synchronize(sd)

    def on_update(self, event, target, **kwargs):
        inspector = target
        provider_info = self.get_or_create(ProviderInfo, inspector.provider_id)
        try: provider_info.provider_repr = repr(inspector.get_value_no_instantiate())
        except Exception: pass
        if event == 'dependency_progress':
            provider_info.state = InstantiationProgress.in_progress

        else: #dependency_final
            if is_obj_ready(inspector.get_value(ready=False)):
                provider_info.state = InstantiationProgress.ready
            else: provider_info.state = InstantiationProgress.not_ready
        try: provider_info.value_injector_id = id(inspector.get_value_no_instantiate().injector)
        except Exception: pass
        self.store_synchronize(provider_info)
        value = inspector.get_value_no_instantiate()
        if isinstance(value, SetupTaskMixin):
            asyncio.ensure_future(self.handle_tasks(value))

    async def handle_task(self, inspector, running=False, should_run=None, exception=None):
        task_info = self.get_or_create(TaskInfo, inspector.instance_id, inspector.stamp)
        task_info.running = running
        if should_run is None:
            try: should_run = await inspector.should_run(ainjector=None)
            except Exception: pass
        if should_run is not None: task_info.should_run = should_run
        try:         task_info.last_run = _iso_time(inspector.last_run)
        except AttributeError: task_info.last_run = None
        task_info.description = inspector.description
        if exception:
            task_info.last_failure = str(exception)
        self.store_synchronize(task_info)
        for subinspector in inspector.subtasks():
            await self.handle_task(subinspector)
            

    async def handle_tasks(self, obj):
        for inspector in obj.inspect_setup_tasks():
            await self.handle_task(inspector)

    async def on_task_event(self, event, target, task, **kwargs):
        exception = None
        should_run = None
        if event == 'task_start': running = True
        else: running = False
        if event == 'task_ran': should_run = False
        if event == 'task_fail':
            exception = kwargs['exception']
        for inspector in  target.inspect_setup_tasks():
            if inspector.task == task:
                return await self.handle_task(
                    inspector,
                    should_run=should_run,
                    exception=exception,
                    running=running)

    def on_instantiation_failed(self, target, scope, target_key, **kwargs):
        failed_instantiation = self.get_or_create(FailedInstantiation,
                                                  id(scope), target_key)
        failed_instantiation.exception = "".join(traceback.format_exception(target.exception))
        failed_instantiation.dependency = target.dependency
        self.store_synchronize(failed_instantiation)
        
    def dump(self, path):
        path = Path(path)
        result: dict[str, list] = {}
        for t, store in self.stores_by_class.items():
            objects = []
            for o in store.values():
                sync_repr = o.to_sync()
                sync_repr['_sync_type'] = o.sync_type
                objects.append(sync_repr)
            result[t.sync_type] = objects
        path.write_text(yaml.dump(result, default_flow_style=False ))
        

    def instrument_injector(self, injector):
        injector.add_event_listener(InjectionKey(Injector), {'add_provider'}, self.on_add_provider)
        injector.add_event_listener(
            InjectionKey(Injector), {'dependency_progress', 'dependency_final'}, self.on_update)
        injector.add_event_listener(
            InjectionKey(SetupTaskMixin),
            {'task_ran', 'task_start', 'task_fail', 'task_should_run'},
            self.on_task_event)
        injector.add_event_listener(
            InjectionKey(Injector), {'dependency_instantiation_failed'}, self.on_instantiation_failed)
        for k, inspector in injector.inspect():
            self.on_add_provider(scope=injector, target_key=inspector.key, other_keys=set(),
                                 inspector=inspector
                                 )
            

carthage_registry = CarthageRegistry()
carthage_registry.register_syncable(SyncOwner.sync_type, SyncOwner)

__all__ += ['carthage_registry']

@dataclasses.dataclass
class ProvidedDependency(StoreInSyncStoreMixin):

    key: InjectionKey = sync_property(constructor=True)
    injector_id: int = sync_property(constructor=True)
    provider_id: int = sync_property(None)

    sync_registry = carthage_registry
    sync_primary_keys = ('injector_id', 'key')

__all__ += ['ProvidedDependency']

class InstantiationProgress(enum.Enum):
    not_instantiated = 0
    in_progress = 1
    not_ready = 2
    ready = 3

__all__ += ['InstantiationProgress']
register_enum(InstantiationProgress)


@dataclasses.dataclass
class ProviderInfo(StoreInSyncStoreMixin):

    id: int = sync_property(constructor=True)
    state: InstantiationProgress= sync_property(InstantiationProgress.not_instantiated)
    
    value_repr:str = sync_property(None)
    value_injector_id: int = sync_property(None)
    

    sync_registry = carthage_registry
    sync_primary_keys = ('id',)
    

__all__ += ['ProviderInfo']

@dataclasses.dataclass
class StaticDependency(StoreInSyncStoreMixin):

    #: The ProviderInfo to which this dependency is attached
    provider_id:int = sync_property(constructor=True)
    key: InjectionKey = sync_property(constructor=True)
    #: The ProviderInfo that provides this dependency
    provided_by:int = sync_property(None)

    sync_primary_keys = ('provider_id', 'key')
    sync_registry = carthage_registry

__all__ += ['StaticDependency']

@dataclasses.dataclass
class InjectorInfo(StoreInSyncStoreMixin):

    id: int = sync_property(constructor=True)
    parent_id: int = sync_property(None)
    description: str = sync_property(None)

    sync_primary_keys = ('id',)
    sync_registry = carthage_registry

@dataclasses.dataclass
class TaskInfo(StoreInSyncStoreMixin):

    instance_id: id = sync_property(constructor=True)
    stamp: str = sync_property(constructor=True)
    description: str = sync_property("")
    running: bool = sync_property(False)
    should_run: bool = sync_property(None)
    last_run: str = sync_property(None)
    last_failure: str = sync_property(None)
    

    sync_primary_keys = ('instance_id', 'stamp')
    sync_registry = carthage_registry
    
__all__ += ['TaskInfo']

def encode_fi_dependency(val):
    return [encode_injection_key(x) for x in val]

def decode_fi_dependency(val):
    return [decode_injection_key(x) for x in val]

@dataclasses.dataclass
class FailedInstantiation(StoreInSyncStoreMixin):

    injector_id: int = sync_property(constructor=True)
    key: InjectionKey = sync_property(constructor=True)
    exception: str = sync_property("")
    dependency: list[InjectionKey] = sync_property(
        dataclasses.field(default_factory=lambda: []),
        encoder=encode_fi_dependency,
        decoder=decode_fi_dependency)
    sync_primary_keys = ('injector_id', 'key')
    sync_registry = carthage_registry

__all__ += ['FailedInstantiation']
