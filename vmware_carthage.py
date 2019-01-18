#!/usr/bin/python3
# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


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
