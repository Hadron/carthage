# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
from ..dependency_injection import *
from ..utils import memoproperty
from ..config import config_defaults, ConfigLayout
from ..setup_tasks import setup_task

from pyVmomi import vim

from ..network import Network, TechnologySpecificNetwork, this_network
from .inventory import VmwareStampable, VmwareFolder, VmwareConnection, wait_for_task

config_defaults.add_config({'vmware': {
    'distributed_switch': None
    }})

@inject(config_layout = ConfigLayout,
        injector = Injector,
        connection = VmwareConnection)
class NetworkFolder(VmwareFolder):
    kind = 'network'

@inject(
        network = this_network,
        config_layout = ConfigLayout,
        injector = Injector,
    connection = VmwareConnection)
class VmwareNetwork(VmwareStampable, TechnologySpecificNetwork):

    '''Abstract Base class representing a VmwareNetwork
'''
    


    def __init__(self, network, *,  config_layout, injector, connection):
        super().__init__()
        self.network = network
        self.config_layout = config_layout
        self.injector = injector.copy_if_owned().claim()
        self.ainjector = self.injector(AsyncInjector)
        self.connection = connection


    def __repr__(self):
        return f"<{self.__class__.__name__} for {self.network}>"

@inject(config_layout = ConfigLayout,
        connection = VmwareConnection,
        injector = Injector,
        network = this_network)
class DistributedPortgroup(VmwareNetwork):
    stamp_type = "portgroup"
    def __init__(self,  **kwargs):
        super().__init__(**kwargs)
        if not self.dvs_object:
            config = self.config_layout.vmware
            raise RuntimeError(f"{config.distributed_switch} distributed switch not found")

    @memoproperty
    def name(self):
        return self.network.name
    
    @setup_task("Creating Distributed Portgroup")
    @inject(loop = asyncio.AbstractEventLoop)
    async def create_portgroup(self, loop):
        cs = vim.dvs.DistributedVirtualPortgroup.ConfigSpec()
        cs.name = self.full_name
        cs.autoExpand = True
        cs.type = "earlyBinding"
        cs.policy = vim.dvs.VmwareDistributedVirtualSwitch.VMwarePortgroupPolicy()
        cs.policy.macManagementOverrideAllowed = True
        default  = vim.dvs.VmwareDistributedVirtualSwitch.VmwarePortConfigPolicy()
        default.macManagementPolicy = vim.dvs.VmwareDistributedVirtualSwitch.MacManagementPolicy()
        learning = vim.dvs.VmwareDistributedVirtualSwitch.MacLearningPolicy()
        learning.enabled = True
        learning.allowUnicastFlooding = True
        default.macManagementPolicy.macLearningPolicy = learning
        cs.defaultPortConfig = default
        task = self.dvs_object.AddDVPortgroup_Task([cs])
        await wait_for_task(task)

    @memoproperty
    def full_name(self):
        prefix = self.config_layout.container_prefix
        return f"{prefix}{self.network.name}"

    @memoproperty
    def dvs_object(self):
        config = self.config_layout.vmware
        return self.connection.content.searchIndex.FindByInventoryPath(
            f"{config.datacenter}/network/{config.distributed_switch}"
            )

    @memoproperty
    def inventory_object(self):
        name = self.full_name
        for p in self.dvs_object.portgroup:
            if p.name == name: return p
        return None

    async def delete(self):
        if not self.inventory_object:
            raise RuntimeError(f"{self} does not exist")
        task = self.inventory_object.Destroy()
        loop = self.injector.get_instance(asyncio.AbstractEventLoop)
        await wait_for_task(task)
        
