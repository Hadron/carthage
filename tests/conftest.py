# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import pytest
from carthage.image import ImageVolume, SshAuthorizedKeyCustomizations
from carthage.vm import InstallQemuAgent
from carthage import base_injector, ConfigLayout, ssh, shutdown_injector
from carthage.dependency_injection import AsyncInjector, DependencyProvider, InjectionKey
from carthage.debian import DebianContainerImage, debian_container_to_vm
from carthage.image import ContainerImage
from carthage.network import Network
from carthage.machine import ssh_origin
from carthage.container import Container, container_image
import os.path
import pytest
import posix
from carthage.pytest import *

pytest_plugins = ('carthage.pytest_plugin',)

def pytest_addoption(parser):
    group = parser.getgroup('podman', 'Podman test options')
    group.addoption('--remote-container-host',
                    action='store_true',
                    help='Use remote container host in AWS for Podman Carthage tests')

@pytest.mark.no_rootless
@pytest.fixture(scope='session')
def test_ainjector(loop):
    if posix.geteuid() != 0:
        pytest.skip("Not running as root; volume tests skipped", )
    try:
        ainjector = base_injector.claim()(AsyncInjector)
        config = loop.run_until_complete(ainjector(ConfigLayout))
        config.delete_volumes = True
        config.persist_local_networking = False
        vol = loop.run_until_complete(ainjector(DebianContainerImage, name="base-debian"))
        vol.config_layout = vol.injector(ConfigLayout)
        vol.config_layout.delete_volumes = False
        loop.run_until_complete(vol.apply_customization(SshAuthorizedKeyCustomizations))
        ainjector.replace_provider(container_image, vol)
        ainjector.replace_provider(loop.run_until_complete(ainjector(Network, 'brint', vlan_id=0)))
        ainjector.replace_provider(ssh_origin, DependencyProvider(None))
        yield ainjector
    finally:
        loop.run_until_complete(shutdown_injector(ainjector))


@pytest.fixture(scope='session')
def vm_image(loop, test_ainjector):
    ainjector = test_ainjector
    debian_container = loop.run_until_complete(ainjector.get_instance_async(container_image))
    config = loop.run_until_complete(ainjector(ConfigLayout))
    image = loop.run_until_complete(ainjector(debian_container_to_vm,
                                              classes="+CLOUD_INIT,SERIAL,OPENROOT",
                                              volume=debian_container,
                                              size="4G",
                                              output=config.vm_image_dir + '/cloud-init.raw'))

    loop.run_until_complete(ainjector.get_instance_async(ssh.SshKey))
    loop.run_until_complete(image.apply_customization(InstallQemuAgent))
    image.config_layout = image.injector(ConfigLayout)
    image.config_layout.delete_volumes = False
    yield image
    image.close()
