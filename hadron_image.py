# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, logging
from carthage.hadron import HadronImageVolume, TestDatabase
from carthage.dependency_injection import AsyncInjector
from carthage import base_injector
from carthage.network import Network
from carthage.container import container_image

async def run():

    ainjector = base_injector(AsyncInjector)
    net = await ainjector(Network, "brint", delete_bridge = False)
    base_injector.add_provider(net)
    volume = await ainjector(HadronImageVolume)
    base_injector.add_provider(container_image, volume)
    container = await ainjector(TestDatabase)
    

logging.getLogger('carthage.container').setLevel(10)
logging.basicConfig()
asyncio.get_event_loop().run_until_complete(run())
