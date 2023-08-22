# Copyright (C)  2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import dataclasses, uuid
from entanglement.interface import *
from entanglement.memory import *
from entanglement.types import register_type


from .dependency_injection import InjectionKey, Injector

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
        provider_info.provider_repr = repr(inspector.get_value_no_instantiate())
        self.store_synchronize(provider_info)
        

    def instrument_injector(self, injector):
        injector.add_event_listener(InjectionKey(Injector), {'add_provider'}, self.on_add_provider)
        for k, inspector in injector.inspect():
            self.on_add_provider(scope=injector, target_key=inspector.key, other_keys=set(),
                                 inspector=inspector
                                 )
            

carthage_registry = CarthageRegistry()
carthage_registry.register_syncable(SyncOwner.sync_type, SyncOwner)

@dataclasses.dataclass
class ProvidedDependency(StoreInSyncStoreMixin):

    key: InjectionKey = sync_property(constructor=True)
    injector_id: int = sync_property(constructor=True)
    provider_id: int = sync_property(None)

    sync_registry = carthage_registry
    sync_primary_keys = ('injector_id', 'key')

@dataclasses.dataclass
class ProviderInfo(StoreInSyncStoreMixin):

    id: int = sync_property(constructor=True)
    provider_repr:str = sync_property(None)

    sync_registry = carthage_registry
    sync_primary_keys = ('id',)
    
