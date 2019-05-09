# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.pytest import *
import os.path, pytest
from carthage.dependency_injection import AsyncInjector, InjectionKey
from carthage import base_injector, network
from carthage.config import ConfigLayout
from carthage.vm import VM
from carthage.network import NetworkConfig
import gc, posix, os

resource_dir = os.path.dirname(__file__)


    

@pytest.fixture()
def ainjector():
    if posix.geteuid() != 0:
        pytest.skip("Not running as root; volume tests skipped", )
    injector = base_injector(AsyncInjector)
    cl = injector.get_instance(InjectionKey(ConfigLayout))
    cl.delete_volumes = True
    nc = NetworkConfig()
    nc.add('eth0', network.external_network_key, None)
    injector.add_provider(nc)
    yield injector
    gc.collect()

@async_test
async def test_vm_config(loop, ainjector, vm_image):
    vm = await ainjector(VM, name = "vm_1", image = vm_image)
    await vm.write_config()
    

@async_test
async def test_vm_test(request, ainjector, vm_image):
    vm = await ainjector(VM, name = "vm_2", image = vm_image)
    breakpoint()
    async with vm.machine_running:
        await vm.ssh_online()
        await vm.rsync(os.path.join(resource_dir, "inner_plugin_test.py"),
                       vm.rsync_path('/'))
        await vm.rsync(os.path.join(resource_dir, "inner_conftest.py"),
                       vm.rsync_path("/conftest.py"))
        await subtest_controller(request, vm, "/inner_plugin_test.py")
        
