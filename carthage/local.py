# Copyright (C) 2021, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import contextlib
from pathlib import Path
from .machine import Machine
from .dependency_injection import *
from . import sh
from .utils import memoproperty, when_needed
from .setup_tasks import SetupTaskMixin
from .network import NetworkLink, BridgeNetwork


class LocalMachineMixin:

    '''A mixin for :class:`machines <Machine>` that represent the locally running system.  If no special behavior is required :class:`LocalMachine` should be used instead.  This mixin is appropriate for cloud machines where it is desirable to retain functionality such as using cloud APIs to examine the configuration, but where  marking the machine as local to prevent shutdown and simplify filesysetm access is desired.

When testing whether a :class:`Machine` is local, test for ``isinstance(machine, LocalMachineMixin)``

    '''
    
    ip_address = "127.0.0.1"
    @contextlib.asynccontextmanager
    async def filesystem_access(self):
        yield "/"

    async def stop_machine(self):
        raise NotImplementedError("Stopping localhost may be more dramatic than desired")

    @property
    def shell(self):
        # We don't actually need to enter a namespace, but this provides similar semantics to what we get with containers
        return sh.nsenter.bake()

    
class LocalMachine(LocalMachineMixin, Machine, SetupTaskMixin):

    '''A machine representing the node on which carthage is running.
    '''

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.running = True
        

    async def async_ready(self):
        await self.resolve_networking()
        await self.run_setup_tasks()
        await super().async_ready()


    async def start_machine(self):
        await self.start_dependencies()
        await super().start_machine()
        return



    async def is_machine_running(self):
        self.running = True
        return True
    
    @memoproperty
    def stamp_path(self):
        return Path(self.config_layout.state_dir+"/localhost")

def process_local_network_config(model):
    '''
    Carthage uses :class:`~BridgeNetwork` to connecgt VMs and containers to networking on the local system.  When a network is contained entirely within one hypervisor, things are easy.  However, if the network configuration interacts with the :class:`~carthage.network.NetworkConfig` of the hypervisor, it's important that networks in NetworkConfigs of VMs on containers match up with networks in the hypervisor's network config.  

    One approach to accomplish this is to set the :obj:`~carthage.modeling.NetworkMobdel.bridge_name` property in the :class:`carthage.model.NetworkModel`.

However if Carthage is configuring the local networking on the hypervisor, then the information is already encoded in the :class:`~NetworkLink` for the hypervisor.  This function finds bridge links in a NetworkConfig corresponding to the machine on which Carthage is running and sets the bridge names that will be used so that Carthage uses the right local bridges.

    '''
    def associate_bridge(net, bridge_name):
        if hasattr(net, 'bridge_name'): return
        net.bridge_name = bridge_name
        net.ainjector.add_provider(InjectionKey(BridgeNetwork),
                                   when_needed(BridgeNetwork, bridge_name = bridge_name, delete_bridge = False))

    from carthage.network.links import BridgeLink
    for l in model.network_links.values():
        if not isinstance(l, BridgeLink): continue
        associate_bridge(l.net, l.interface)
        
__all__ = ['LocalMachineMixin', 'LocalMachine', 'process_local_network_config']
