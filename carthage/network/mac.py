# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import os, random, yaml
from pathlib import Path
from ..dependency_injection import *
from ..config import ConfigLayout

def random_mac_addr():
    mac = [random.randint(0,255) for x in range(6)]
    mac[0] &= 0xfc #Make it locally administered
    macstr = [format(m, "02x") for m in mac]
    return ":".join(macstr)

__all__ = ['random_mac_addr']


@inject_autokwargs(config_layout = ConfigLayout,
                   injector = Injector)
class MacStore(Injectable, dict):

    def __init__(self, **kwargs):
        from .base import NetworkConfig
        super().__init__(**kwargs)
        state_dir = Path(self.config_layout.state_dir)
        self.path = state_dir/"macs.yml"
        self.load()
        self.injector.parent_injector.add_event_listener(InjectionKey(NetworkConfig),
                                         "resolved", self._resolved_event)

    def _resolved_event(self, key, event, target, *args, **kwargs):
        self.persist()

    def load(self):
        def recurse(current, base_key):
            for k, v in current.items():
                if isinstance(v, dict):
                    recurse(v, base_key + (k,))
                else:
                    self[base_key + (k,)] = v
                    
        if self.path.exists():
            yaml_dict = yaml.safe_load(self.path.read_text())
            assert isinstance(yaml_dict, dict)
            recurse(yaml_dict, tuple())
            
    def persist(self):
        def recurse(key, current, value):
            if isinstance(key, tuple):
                if len(key) == 1:
                    current[key[0]] = value
                else:
                    current = current.setdefault(key[0], {})
                    recurse(key[1:], current, value)
            else: current[key] = value
        new_path = self.path.with_suffix(".yml.new")
        os.makedirs(self.path.parent, exist_ok = True)
        result = {}
        for k, v in self.items():
            recurse(k, result, v)
        new_path.write_text(yaml.dump(result, default_flow_style = False))
        new_path.replace(self.path)

    def __getitem__(self, k):
        if k in self: return super().__getitem__(k)
        res = self[k] = random_mac_addr()
        return res
    
__all__ += ['MacStore']

from ..machine import AbstractMachineModel
@inject(model = AbstractMachineModel,
        store = MacStore)
def persistent_random_mac(interface, model, store):
    return store[(model.name, interface)]

__all__ += ['persistent_random_mac']
