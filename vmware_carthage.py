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
from carthage.vmware import VmFolder, Vm, VmfsDataStore, VmdkTemplate, NfsDataStore, VmTemplate
from carthage.vmware.image import vm_storage_key
from carthage.hadron.vmware import HadronVmdkBase
from carthage.config import ConfigIterator

@inject(ainjector = AsyncInjector)
async def run(ainjector):
    futures = []
    config = await ainjector(ConfigLayout)
    ainjector.add_provider(VmfsDataStore)
    vmdk_template = await ainjector(create_template)
    template = await ainjector(VmTemplate, disk = vmdk_template)
    vm = await ainjector(Vm, "carthage-test.cambridge.aces-aoe.com", template = template)
    await ainjector(vm._ansible_op, state ='poweredon', force = True)
    breakpoint()
    if futures:
        await asyncio.wait(futures)

@inject(parent = Injector)
async def create_template(parent):
    injector = parent(Injector).claim()
    ainjector = injector(AsyncInjector)
    injector.add_provider(vm_storage_key, partial_with_dependencies(ConfigIterator, prefix="vmware.image_datastore."), allow_multiple = True)
    injector.add_provider(NfsDataStore)
    image = await ainjector(HadronVmdkBase)
    vmdk_template = await ainjector(VmdkTemplate, image)
    return vmdk_template



    
parser = carthage.utils.carthage_main_argparser()
carthage.utils.carthage_main_setup(parser)
carthage.utils.carthage_main_run(run)
