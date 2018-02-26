# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, logging
from carthage.hadron_layout import database_key
from carthage.dependency_injection import AsyncInjector, InjectionKey
from carthage import base_injector, ssh
from carthage.hadron import HadronVmImage

async def run():

    ainjector = base_injector(AsyncInjector)
    await ainjector(HadronVmImage)


#logging.getLogger('carthage.container').setLevel(7)
#logging.getLogger('carthage.dependency_injection').setLevel(10)
logging.basicConfig(level = 'INFO')
asyncio.get_event_loop().run_until_complete(run())
