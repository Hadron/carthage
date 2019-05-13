# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, pytest
from carthage.image import image_factory, SshAuthorizedKeyCustomizations
from carthage.vm import InstallQemuAgent
from carthage import base_injector, ConfigLayout
from carthage.dependency_injection import AsyncInjector, DependencyProvider
from carthage.image import ContainerImage
from carthage.network import Network
from carthage.machine import ssh_origin
from carthage.container import Container, container_image
import os.path, pytest, posix
from carthage.pytest import *

pytest_plugins = ('carthage.pytest_plugin',)

@pytest.fixture(scope = 'session')
@async_test
async def test_ainjector(loop):
    if posix.geteuid() != 0:
        pytest.skip("Not running as root; volume tests skipped", )
    ainjector = base_injector(AsyncInjector)
    config = await ainjector(ConfigLayout)
    config.delete_volumes = True
    vol = await ainjector(ContainerImage, name = "base")
    base_injector.add_provider(container_image, vol)
    base_injector.add_provider(await ainjector(Network,'brint', vlan_id = 0))
    ainjector.replace_provider(ssh_origin, DependencyProvider(None))
    return ainjector






@pytest.fixture(scope = 'session')
def vm_image( loop, test_ainjector):
    ainjector = test_ainjector
    image = loop.run_until_complete(ainjector(image_factory, name = "base"))
    loop.run_until_complete(image.apply_customization(SshAuthorizedKeyCustomizations))
    loop.run_until_complete(image.apply_customization(InstallQemuAgent))
    yield image
    image.close()
    

                

        
            
