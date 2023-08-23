# Copyright (C)  2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import dataclasses
import enum
from pathlib import Path
import uuid
import yaml

from entanglement.interface import *
from entanglement.memory import *
from entanglement.filter import *
from entanglement.types import register_type, register_enum


from ..dependency_injection import InjectionKey, Injector, is_obj_ready, get_dependencies_for

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
        for k in other_keys | {target_key}:
            o = ProvidedDependency(injector_id=injector_id, key=k, provider_id=inspector.provider_id)
            self.store_synchronize(o)
        provider_info = self.get_or_create(ProviderInfo, inspector.provider_id)
        try: provider_info.provider_repr = repr(inspector.get_value_no_instantiate())
        except Exception:
            provider_info.provider_repr = None
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
            if is_obj_ready(inspector.get_value()):
                provider_info.state = InstantiationProgress.ready
            else: provider_info.state = InstantiationProgress.not_ready
        self.store_synchronize(provider_info)
        
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
        path.write_text(yaml.dump(result, default_flow_style=False))
        

    def instrument_injector(self, injector):
        injector.add_event_listener(InjectionKey(Injector), {'add_provider'}, self.on_add_provider)
        injector.add_event_listener(
            InjectionKey(Injector), {'dependency_progress', 'dependency_final'}, self.on_update)
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
    
    provider_repr:str = sync_property(None)

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
