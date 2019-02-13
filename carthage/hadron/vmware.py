# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import sys
from .images import  HadronVmImage
from ..vmware.image import VmdkTemplate, image_datastore_key
from ..vmware import VmTemplate, Vm, VmwareDataStore, VmFolder
from ..dependency_injection import AsyncInjector, inject, Injector, InjectionKey
from ..utils import when_needed
from ..config import ConfigLayout
from .. import sh
from ..machine import ContainerCustomization, customization_task
from ..setup_tasks import setup_task
from ..container import Container, container_image, container_volume
from ..network import Network, NetworkConfig, external_network_key


@inject(config_layout = ConfigLayout,
        injector = Injector)
class HadronVmwareCustomization(ContainerCustomization):
    description = "Customizations for ACES on Vmware"

    @setup_task("install-vm-tools")
    async def install_vm_tools(self):
        await self.container_command("/usr/bin/apt", "-y", "install", "open-vm-tools")


@inject(config_layout = ConfigLayout,
        ainjector = AsyncInjector,
        store = image_datastore_key)
class HadronVmdkBase(HadronVmImage):

    def __init__(self, *, ainjector, config_layout,
                 store,
                 name = "aces-vmdk", **kwargs):
        super().__init__(**kwargs, name = name,
                         ainjector = ainjector, config_layout = config_layout,
                         path = store.vmdk_path)

    vmware_customization = customization_task(HadronVmwareCustomization)

@when_needed
@inject(config = ConfigLayout,
        injector = Injector)
async def carthage_trunk_network(ignore, *, config, injector):
    return injector(Network, "Carthage Trunk", vlan_id = (config.vlan_min, config.vlan_max))

carthage_vmware_netconfig = NetworkConfig()
carthage_vmware_netconfig.add('eth0', external_network_key, None)
carthage_vmware_netconfig.add('eth1', carthage_trunk_network, None)

aces_vmdk_image = when_needed(HadronVmdkBase)

@when_needed
@inject(ainjector = AsyncInjector)
async def aces_vm_template(ainjector):
    image = await ainjector(aces_vmdk_image)
    vmdk = await ainjector(VmdkTemplate, image)
    template = await ainjector(VmTemplate, disk = vmdk)
    return await ainjector(VmTemplate, disk = None, template = template, name = f"Clonable {template.name}")


@inject(
    config_layout = ConfigLayout,
    storage = VmwareDataStore,
    folder = InjectionKey(VmFolder, optional = True),
    injector = Injector
    )
class CarthageVm(Vm):

    nested_virt = True

    def __init__(self, name, template, *,
                 config_layout, storage, injector, folder):
        super().__init__(name, template, injector = injector,
                         config = config_layout,
                         network_config = carthage_vmware_netconfig, storage = storage, folder = folder)
        self.cpus = 8
        self.memory = 30000
        self.disk_size = 60

        
