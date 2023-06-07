# Copyright (C) 2021, 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import collections.abc
import contextlib
import os
import random
import yaml
from pathlib import Path
from ..dependency_injection import *
from ..config import ConfigLayout
import carthage.kvstore


def random_mac_addr():
    mac = [random.randint(0, 255) for x in range(6)]
    mac[0] &= 0xfc  # Make it locally administered
    macstr = [format(m, "02x") for m in mac]
    return ":".join(macstr)


__all__ = ['random_mac_addr']


@inject_autokwargs(config_layout=ConfigLayout,
                   injector=Injector,
                   kvstore=carthage.kvstore.KvStore)
class MacStore(Injectable):

    # If dealing with potential MAC conflicts is important, then
    # rather than using KvStore directly, rewrite to use
    # HintedAssignments.

    def __init__(self, **kwargs):
        from .base import NetworkConfig
        super().__init__(**kwargs)
        state_dir = Path(self.config_layout.state_dir)
        self.path = state_dir / "macs.yml"
        self.domain = self.kvstore.domain('mac', True)
        self.load()
        

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


    def __contains__(self, k):
        k = self.handle_tuple_key(k)
        res = self.domain.get(k)
        return True if res else False
    
    def __getitem__(self, k):
        k = self.handle_tuple_key(k)
        res = self.domain.get(k)
        if res: return res
        res = random_mac_addr()
        self[k] = res
        return res

    def __setitem__(self, k, v):
        k = self.handle_tuple_key(k)
        self.domain.put(k, v, overwrite=True)
        return v

    def handle_tuple_key(self, k):
        if isinstance(k, str): return k
        if not isinstance(k, collections.abc.Sequence): return k
        k_new = tuple(map( lambda component: component.replace('|','||'), k))
        return "|".join(k)
    
__all__ += ['MacStore']

from ..machine import AbstractMachineModel
from ..local import LocalMachineMixin


@inject(model=AbstractMachineModel,
        store=MacStore)
def persistent_random_mac_always(interface, model, store):
    '''Return a persistant random mac address, even for LocalMachine'''
    return store[(model.name, interface)]

__all__ += ['persistent_random_mac_always']

@inject(model=AbstractMachineModel,
        store=MacStore)
def persistent_random_mac(interface, model, store):
    '''Return a persistent random MAC address if one is already stored, or if the machine type is not a local machine.  In other words, unless the database is pre-seeded, return None for LocalMachine.'''
    if (model.name, interface) in store: return store[(model.name, interface)]
    try:
        machine_type = model.machine_type
        if issubclass(machine_type, LocalMachineMixin): return None
    except AttributeError: pass
    return store[(model.name, interface)]


__all__ += ['persistent_random_mac']


def find_mac_first_member(link):
    '''Return the MAC Address of the first member of this link. Typically this function is not called directly but instead is called when a bridge, bond or VLAN link's *mac* property is set to ``inherit``.
'''
    if link.mac != 'inherit':
        raise ValueError("Intended to be used on alink whose MAC is inherit")
    orig_link = link
    while link:
        if not link.member_links:
            raise ValueError(f"Could not find MAC for {orig_link}")
        link = link.member_links[0]
        if link.mac != 'inherit':
            return link.mac


__all__ += ['find_mac_first_member']
