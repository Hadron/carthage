# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from test_helpers import *
import os.path, pytest
from carthage.dependency_injection import AsyncInjector, InjectionKey
from carthage import base_injector
from carthage.config import ConfigLayout
from carthage.vm import VM
from carthage.network import NetworkConfig
import posix, gc

    

@pytest.fixture()
def ainjector():
    if posix.geteuid() != 0:
        pytest.skip("Not running as root; volume tests skipped", )
    injector = base_injector(AsyncInjector)
    cl = injector.get_instance(InjectionKey(ConfigLayout))
    cl.delete_volumes = True
    nc = NetworkConfig()
    nc.add('eth0', InjectionKey('external-network'), None)
    injector.add_provider(nc)
    yield injector
    gc.collect()

@async_test
async def test_vm_config(loop, ainjector, vm_image):
    vm = await ainjector(VM, name = "vm_1", image = vm_image)
    await vm.write_config()
    
