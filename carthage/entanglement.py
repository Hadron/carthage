# Copyright (C)  2022, Hadron Industries, Inc.
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
    
    def on_add_provider(self, scope,  other_keys, target_key, **kwargs):
        if not self.manager: return
        injector_id = self.injector_id(scope)
        for k in other_keys | {target_key}:
            o = ProvidedDependency(injector_id=injector_id, key=k)
            self.store_synchronize(o)

    def instrument_injector(self, injector):
        injector.add_event_listener(InjectionKey(Injector), {'add_provider'}, self.on_add_provider)
        for k in injector.filter(target=None,
                                 predicate=lambda k:True,
                                 stop_at=injector):
            self.on_add_provider(scope=injector, target_key=k, other_keys=set())
            

carthage_registry = CarthageRegistry()
carthage_registry.register_syncable(SyncOwner.sync_type, SyncOwner)
@dataclasses.dataclass
class ProvidedDependency(StoreInSyncStoreMixin):

    key: InjectionKey = sync_property(constructor=True)
    injector_id: int = sync_property(constructor=True)


    sync_registry = carthage_registry
    sync_primary_keys = ('injector_id', 'key')

