#!/usr/bin/python3
# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


import asyncio, carthage, carthage.utils
from carthage import base_injector, inject, AsyncInjector, ConfigLayout, Injector, partial_with_dependencies
from carthage.vmware import VmFolder, Vm, VmfsDataStore, VmdkTemplate, NfsDataStore, VmTemplate, inventory, DistributedPortgroup
from carthage.vmware.image import vm_storage_key
from carthage.hadron.vmware import HadronVmdkBase
from carthage.config import ConfigIterator
from carthage.network import external_network_key

@inject(ainjector = AsyncInjector)
async def run(ainjector):
    futures = []
    config = await ainjector(ConfigLayout)
    net = await ainjector.get_instance_async(external_network_key)
    pg = await net.access_by(DistributedPortgroup)
    vmdk_template = await ainjector(create_template)
    template = await ainjector(VmTemplate, disk = vmdk_template)
    vm = await ainjector(Vm, "carthage-test.cambridge.aces-aoe.com", template = template)
    try: await vm.start_machine()
    except TimeoutError:
        import traceback
        traceback.print_exc()
    breakpoint()
    if futures:
        await asyncio.wait(futures)

@inject(parent = Injector)
async def create_template(parent):
    injector = parent(Injector).claim()
    ainjector = injector(AsyncInjector)
    image = await ainjector(HadronVmdkBase)
    vmdk_template = await ainjector(VmdkTemplate, image)
    return vmdk_template



    
parser = carthage.utils.carthage_main_argparser()
carthage.utils.carthage_main_setup(parser)
carthage.utils.carthage_main_run(run)
