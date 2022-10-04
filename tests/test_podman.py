# Copyright (C)  2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.podman import *
from carthage.oci import oci_container_image
from carthage.modeling import *
from carthage import *
from carthage.pytest import *
class podman_layout(CarthageLayout):
    layout_name = 'podman'

    class foo(MachineModel):

        name = 'foo.com'

        add_provider(machine_implementation_key, dependency_quote(PodmanContainer))
        add_provider(oci_container_image, 'debian:latest')
        oci_interactive = True


@async_test
async def test_podman_create(ainjector):
    l= await ainjector(podman_layout)
    ainjector = l.ainjector
    machine = l.foo.machine
    await machine.async_become_ready()
    assert await machine.find()
    machine.stop_timeout = 1
    async with machine.machine_running(ssh_online=False):
        assert await machine.is_machine_running()
    await machine.delete()
    assert not await machine.find()
    

@async_test
async def test_container_exec(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    machine = l.foo.machine
    try:
        await machine.async_become_ready()
        machine.stop_timeout = 1
        async with machine.machine_running(ssh_online=False):
            assert 'root' in str(await machine.container_exec('ls'))
    finally:
        await machine.delete()
        
