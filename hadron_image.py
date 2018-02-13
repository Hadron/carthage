# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
from carthage.hadron import HadronImageVolume
from carthage.dependency_injection import AsyncInjector
from carthage import base_injector

async def run():
    ainjector = base_injector(AsyncInjector)
    volume = await ainjector(HadronImageVolume)

asyncio.get_event_loop().run_until_complete(run())
