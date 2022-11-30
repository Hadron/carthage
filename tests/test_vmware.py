# Copyright (C) 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import os
import os.path
import pytest
from carthage.dependency_injection import *
from carthage.pytest import *
from carthage.vmware import *
from carthage import ConfigLayout
from pyVmomi import vim
from carthage.network import NetworkConfig, external_network_key


@pytest.fixture()
@async_test
@inject(config=ConfigLayout)
async def config(config):
    vmc = config.vmware
    for k in ('hostname', 'password', 'username', 'folder', 'cluster'):
        if getattr(vmc, k, None) is None:
            pytest.skip("Vmware Carthage is inadequately configured")
    return config


@pytest.fixture()
def vm_folder(ainjector, loop, config):
    f1 = loop.run_until_complete(ainjector(VmFolder))
    f2 = loop.run_until_complete(ainjector(VmFolder, parent=f1, name="test_vmware"))
    yield f2
    loop.run_until_complete(f2.delete())


@async_test
async def test_vm_create(vm_folder, ainjector):
    v = await ainjector(Vm, parent=vm_folder, name="blah")


@async_test
async def test_clone_vm(ainjector, vm_folder):
    v = await ainjector(VmTemplate, disk=None, parent=vm_folder, name="blah2", template=None)
    v2 = await ainjector(Vm, template=v, parent=vm_folder, name="clone1")


@async_test
async def test_clone_increase_disk_size(ainjector, vm_folder):
    class VmBigDisk(Vm):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.disk_size = self.disk_size * 2
            self.template_snapshot = None

    v = await ainjector(VmTemplate, disk=None, parent=vm_folder, name="blah3", template=None)
    v2 = await ainjector(VmBigDisk, template=v, parent=vm_folder, name="clone1big")
    d = 0
    for dev in v2.mob.config.hardware.device:
        if isinstance(dev, vim.vm.device.VirtualDisk):
            d = dev.capacityInBytes
            break
    assert d == v2.disk_size
    v3 = await ainjector(Vm, template=v2, name="clone of clone", parent=vm_folder)


@async_test
async def test_vm_with_network(ainjector, vm_folder):
    net_config = await ainjector(NetworkConfig)
    net_config.add('eth0', external_network_key, None)
    v = await ainjector(VmTemplate, template=None, disk=None, name="blah-net1", network_config=net_config)
    v2 = await ainjector(Vm, template=v, name="blah-net-clone", network_config=net_config)
