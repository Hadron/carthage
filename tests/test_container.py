# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import logging, time
from carthage.image import ImageVolume
from carthage.network import Network
from carthage.container import Container, container_image
from test_helpers import *
import os.path, pytest, posix
from carthage import base_injector, AsyncInjector

@pytest.fixture(scope = 'module')
@async_test
async def injector(loop):
    if posix.geteuid() != 0:
        pytest.skip("Not running as root; volume tests skipped", )
    ainjector = base_injector(AsyncInjector)
    vol = await ainjector(ImageVolume, name = "base")
    base_injector.add_provider(container_image, vol)
    base_injector.add_provider(await ainjector(Network,'brint', delete_bridge = False))
    return base_injector

@pytest.fixture()
def ainjector(injector):
    return injector(AsyncInjector)

@pytest.fixture()
def container(ainjector, loop):
    container = loop.run_until_complete(ainjector(Container, name = "container_1"))
    yield container
    if container.running:
        loop.run_until_complete(container.stop_container())

@async_test
async def test_start_container(container, loop):
    await container.start_container()

@async_test
async def test_container_running(container, loop):
    async with container.container_running:
        container.shell("/bin/ls")
        


    
    

    
logging.getLogger('carthage.container').setLevel(10)
logging.basicConfig()
