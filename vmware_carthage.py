#!/usr/bin/python3

import carthage, carthage.utils
from carthage import base_injector, inject, AsyncInjector, ConfigLayout
from carthage.vmware import VmFolder

@inject(ainjector = AsyncInjector)
async def run(ainjector):
    config = await ainjector(ConfigLayout)
    folder = await ainjector(VmFolder, config.vmware.folder)
    breakpoint()

parser = carthage.utils.carthage_main_argparser()
carthage.utils.carthage_main_setup(parser)
carthage.utils.carthage_main_run(run)
