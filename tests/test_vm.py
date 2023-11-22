# Copyright (C) 2018, 2019, 2020, 2021, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.pytest import *
import os.path
import pytest
from carthage.dependency_injection import *
from carthage.dependency_injection import DependencyProvider
from carthage import base_injector, network, rsync_git_tree, V4Config, vm, Machine, sh
from carthage.network import random_mac_addr
from carthage.config import ConfigLayout
from carthage.vm import VM, vm_image
from carthage.network import NetworkConfig
from carthage.machine import ssh_origin
import carthage.ansible
from carthage.modeling import *
import gc
import posix
import os

resource_dir = os.path.dirname(__file__)

pytestmark = pytest.mark.no_rootless

@pytest.fixture()
def ainjector():
    if posix.geteuid() != 0:
        pytest.skip("Not running as root; volume tests skipped", )
    try:
        sh.virsh
    except BaseException:
        pytest.skip("libvirt not installed")
    injector = base_injector.claim()(AsyncInjector)
    cl = injector.get_instance(InjectionKey(ConfigLayout))
    cl.delete_volumes = True
    nc = NetworkConfig()
    nc.add('eth0', network.external_network_key, None)
    injector.add_provider(nc)
    injector.replace_provider(ssh_origin, DependencyProvider(None))
    yield injector
    gc.collect()


@async_test
async def test_vm_config(loop, ainjector, vm_image):
    vm = await ainjector(VM, name="vm_1", image=vm_image)
    await vm.write_config()


@async_test
async def test_vm_test(request, ainjector, vm_image):
    with TestTiming(300):
        vm = await ainjector(VM, name="vm_2", image=vm_image)
    vm.ssh_rekeyed()
    assert vm.config_layout.delete_volumes
    with TestTiming(400):
        async with vm.machine_running():
            await vm.ssh_online()
            await vm.ssh("apt-get update")
            await vm.ssh("apt-get -y install python3-pytest ansible rsync python3-mako python3-sh python3-lmdb locales-all")
            await ainjector(rsync_git_tree, resource_dir, vm.rsync_path('/carthage'))
            await subtest_controller(request, vm, "/carthage/tests/inner_plugin_test.py",
                                     python_path="/carthage")
            # We also test ansible here because we already have a VM up and running
            await ainjector(
                carthage.ansible.run_playbook,
                ["vm"],
                "/carthage/tests/resources/test_playbook.yml",
                inventory="/carthage/tests/resources/inventory.txt",
                origin=vm)


@async_test
async def test_cloud_init(test_ainjector, vm_image):
    try:
        sh.virsh
    except Exception:
        pytest.skip("libvirt not installed")
    ainjector = test_ainjector
    config = ainjector.injector(ConfigLayout)
    assert config.delete_volumes

    class layout(CarthageLayout):
        name = "test_cloud_init"

        @provides("test_net")
        class test_net(NetworkModel, AsyncInjectable):
            name = "test_net"

            async def async_ready(self):
                await super().async_ready()
                from carthage.network import BridgeNetwork
                net = await self.ainjector(self.access_by, BridgeNetwork)
                sh.ip(
                    "addr",
                    "add", "10.2.0.1/24", "dev", net.bridge_name)

        class net_config(NetworkConfigModel):
            add('eth0', mac=random_mac_addr(), net=InjectionKey("test_net", _ready=True),
                v4_config=V4Config(address="10.2.0.2",
                                   network="10.2.0.0/24"))

        add_provider(machine_implementation_key, dependency_quote(VM))
        add_provider(carthage.vm.vm_image, vm_image)

        class vm_3(MachineModel):
            name = "vm-3"
            ip_address = "10.2.0.2"
            cloud_init = True
    ainjector.add_provider(layout)
    l = await ainjector.get_instance_async(layout)
    ainjector = l.ainjector
    with TestTiming(300):
        m = await ainjector.get_instance_async(InjectionKey(Machine, host="vm-3"))
        m.ssh_rekeyed()
        async with m.machine_running(ssh_online=True):
            pass
