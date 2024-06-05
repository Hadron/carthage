# Copyright (C)  2022, 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import json
import os
import pytest
import shutil
from pathlib import Path
from carthage.podman import *
from carthage.oci import oci_container_image, OciExposedPort, OciMount
from carthage.ansible import *
from carthage.container import container_image
from carthage.modeling import *
from carthage.image import SshAuthorizedKeyCustomizations
from carthage.ssh import SshKey
from carthage import *
from carthage.become_privileged import BecomePrivilegedMixin
from carthage.machine import FilesystemCustomization
import carthage.sh
from carthage.pytest import *

state_dir = Path(__file__).parent.joinpath("test_state")



@pytest.fixture(scope='session')
def enable_podman():
    import carthage.plugins
    base_injector(carthage.plugins.load_plugin, 'carthage.podman')

@pytest.fixture(scope='module')
def ainjector(enable_podman, pytestconfig, loop):
    ainjector = base_injector.claim('test_podman.py')(AsyncInjector)
    config = ainjector.injector(carthage.ConfigLayout)
    config.state_dir = state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    if pytestconfig.getoption('remote_container_host'):
        from podman_remote_host import container_host
        ainjector.add_provider(ssh_jump_host, injector_access(podman_container_host))
    else:
        container_host = LocalPodmanContainerHost
    ainjector.add_provider(podman_container_host, container_host)
    container_host_instance = loop.run_until_complete(ainjector.get_instance_async(InjectionKey(container_host, _ready=False)))
    try:
        host_machine = loop.run_until_complete(container_host_instance.ainjector.get_instance_async(InjectionKey(Machine, _ready=False)))
    except KeyError:
        host_machine = None
    try:
        if host_machine:
            loop.run_until_complete(host_machine.async_become_ready())
    except Exception as e:
        logger.exception('Error bringing container_host to ready:')
        try:
            logger.info('Deleting %s', host_machine)
            loop.run_until_complete(host_machine.delete())
            host_machine = None
        except Exception:
            logger.exception('Error deleting container_host')
        raise e
    yield ainjector
    if host_machine:
        logger.info('Deleting %s', host_machine)
        try:
            loop.run_until_complete(host_machine.ainjector(host_machine.delete))
        except Exception as e:
            logger.exception('error cleaning up container host')
        
    shutil.rmtree(state_dir, ignore_errors=True)
    ainjector.close()

@pytest.fixture()
def layout_fixture(ainjector):
    loop = ainjector.loop
    ainjector.replace_provider(podman_layout)
    layout = loop.run_until_complete(ainjector.get_instance_async(CarthageLayout))
    yield layout
    try: loop.run_until_complete(
            layout.podman_net.instantiated.delete(force=True))
    except Exception: pass


class podman_layout(CarthageLayout):
    layout_name = 'podman'

    add_provider(machine_implementation_key, dependency_quote(PodmanContainer))
    add_provider(oci_container_image, 'debian:latest')
    #add_provider(ansible_log, "/tmp/ansible.log")

    oci_interactive = True

    @provides('podman_net')
    class podman_net(NetworkModel):
        v4_config = V4Config(
            network='10.66.0.0/24',
            pool=('10.66.0.5', '10.66.0.20'))

        instantiated = injector_access(carthage.podman.PodmanNetwork)
    class FromScratchDebian(PodmanFromScratchImage):
        oci_image_cmd = 'bash'
        oci_image_tag = 'localhost/from_scratch_debian'

    class DebianWithAuthorizedKeys(PodmanImage):
        oci_image_tag = 'localhost/authorized-debian:latest'
        authorized_keys = image_layer_task(SshAuthorizedKeyCustomizations)

    class ImageModelCustomizations(PodmanImageModel):

        oci_image_tag = 'localhost/carthage_podman:image_model'

        class fs_cust(FilesystemCustomization):

            @setup_task("Test filesystem customization in image model")
            def test_fscust(self): pass

        class container_cust(ContainerCustomization):

            @setup_task("Test container customization")
            def test_container_cust(self): pass

    class foo(MachineModel):

        name = 'foo.com'

    class ssh_test(MachineModel):
        name = 'ssh-test.foo.com'
        ip_address = '127.0.0.1'
        podman_options = ('--privileged',)
        add_provider(OciExposedPort(22))

    class mount_test(MachineModel):
        add_provider(OciMount(
            mount_type='bind',
            destination='/host',
            source='/',
        ))

    class ansible_test(MachineModel):

        class cust(FilesystemCustomization):

            @setup_task("Install Ansible")
            async def install_ansible(self):
                await self.run_command('apt', 'update')
                await self.run_command('apt', '-y', 'install', 'ansible')

            do_roles = ansible_role_task(os.path.dirname(__file__) + "/resources/test_ansible_role")

    class stamps_discarded(MachineModel):

        #A machine to confirm that stamps are ignored after a machine is deleted
        task_called = False
        class cust(FilesystemCustomization):

            @setup_task("Set variable")
            def set_variable(self):
                self.host.model.task_called = True

    class TrueImage(ContainerfileImageModel):

        oci_image_tag = 'localhost/true:latest'
        container_context = 'resources/true_container'

    class DynamicContainerFileImage(ContainerfileImage):
        oci_image_tag = 'localhost/dynamic:latest'
        container_context = 'resources/dynamic_container'

        @setup_task('Create dynamic script')
        def create_dynamic_script(self):
            self.output_path.joinpath('script').write_text('''\
#!/bin/sh
exit 0
            ''')


    class true_machine(MachineModel):
        add_provider(oci_container_image, injector_access(TrueImage))

        name = 'true-machine'

    class dynamic_machine(MachineModel):
        add_provider(oci_container_image, injector_access(DynamicContainerFileImage))

        name = 'dynamic-machine'

    class pod_group(ModelGroup):
        add_provider(OciExposedPort(22))

        @provides(InjectionKey(PodmanPod))
        class pod(PodmanPod):
            name = 'carthage-test-pod'

        class pod_member(MachineModel):
            pass

    class networked_container(MachineModel):

        class config(NetworkConfigModel):
            add('eth0',
                mac=None,
                net=podman_net)


    class net_pod(PodmanPodModel):

        name = 'net_test'
        class config(NetworkConfigModel):
            add('eth0', mac=None, net=podman_net)

        class container(MachineModel):
            pass

    class volume(PodmanVolume):
        name = 'test_volume'


    class populated_volume(PodmanVolume):

        name = 'populated_volume'

        @setup_task("Write a file")
        async def write_file(self):
            async with self.filesystem_access() as path:
                p = path/"foo"
                p.write_text("some text goes here\n")

    class populated_volume_container(MachineModel):
        name = 'populated-volume-container'

        add_provider(OciMount(
            source=InjectionKey('populated_volume'),
            destination='/volume'))
        
@async_test
async def test_podman_create(ainjector):
    l = await ainjector(podman_layout)
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


@async_test
async def test_container_ssh(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    machine = l.ssh_test.machine
    await ainjector.get_instance_async(SshKey)
    try:
        await machine.async_become_ready()
        machine.stop_timeout = 1
        async with machine.machine_running(ssh_online=False):
            await machine.container_exec('apt', 'update')
            await machine.container_exec(
                'apt', '-y', '--no-install-recommends', 'install', 'openssh-server')
            await machine.apply_customization(SshAuthorizedKeyCustomizations)
            await machine.container_exec('mkdir', '/run/sshd')
            await machine.container_exec('/usr/sbin/sshd')
            with TestTiming(400):
                await machine.ssh_online()
    finally:
        await machine.delete()


@async_test
async def test_podman_image(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    await l.DebianWithAuthorizedKeys.async_become_ready()


@async_test
async def test_podman_mount(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    machine = l.mount_test.machine
    assert machine.mounts
    try:
        machine.stop_timeout = 0
        await machine.async_become_ready()
        async with machine.machine_running(ssh_online=False):
            await machine.container_exec('ls', '/host/etc')
    finally:
        await machine.delete()


@pytest.mark.no_rootless
@async_test
async def test_from_scratch_image(test_ainjector):
    l = await test_ainjector(podman_layout)
    ainjector = l.ainjector
    config = await test_ainjector(ConfigLayout)
    config.delete_volumes = False
    ainjector.add_provider(podman_image_volume_key, injector_access(container_image))
    await l.FromScratchDebian.async_become_ready()


@async_test
async def test_podman_ansible(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    machine = l.ansible_test.machine
    try:
        await machine.async_become_ready()
    finally:
        await machine.delete()


@pytest.mark.requires_podman_pod
@async_test
async def test_podman_pod(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    pg = l.pod_group
    machine = pg.pod_member.machine
    try:
        await machine.async_become_ready()
    finally:
        try:
            await pg.pod.delete(force=True)
        except Exception:
            pass

@async_test
async def test_stamps_ignored(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    assert l.stamps_discarded.task_called is False
    await l.stamps_discarded.machine.async_become_ready()
    assert l.stamps_discarded.task_called is True
    await l.stamps_discarded.machine.delete()
    # Podman doesn't work very well if you do something to an instance after delete
    # So we instantiate a second instance directly with the class.
    # Note that we're instantiating to ready since we call the injector directly
    stamps_discarded_2 = await ainjector(podman_layout.stamps_discarded)
    try:
        await stamps_discarded_2.machine.async_become_ready()
        assert stamps_discarded_2.task_called
    finally:
        try: await stamps_discarded_2.machine.delete()
        except Exception: pass

@async_test
async def test_containerfile_image(ainjector):
    l = await ainjector(podman_layout)
    ainjector = l.ainjector
    try:
        await l.true_machine.machine.async_become_ready()
    finally:
        try: await l.true_machine.machine.delete()
        except Exception: pass

@async_test
async def test_dynamic_containerfile_image(layout_fixture):
    l = layout_fixture
    ainjector = l.ainjector
    try:
        await l.dynamic_machine.machine.async_become_ready()
    finally:
        try: await l.dynamic_machine.machine.delete()
        except Exception: pass

@async_test
async def test_podman_container_network(layout_fixture):
    "Test networking on a single container"
    layout = layout_fixture
    try:
        await layout.networked_container.machine.async_become_ready()
    except InjectionFailed as e:
        if isinstance(e.__cause__,NotImplementedError):
            pytest.xfail("Podman too old")
    finally:
        try: await layout.networked_container.machine.delete()
        except Exception: pass

@pytest.mark.requires_podman_pod
@async_test
async def test_podman_pod_network(layout_fixture):
    "Test networking in a pod"
    layout = layout_fixture
    try:
        try: await layout.net_pod.container.machine.async_become_ready()
        except InjectionFailed as e:
            if isinstance(e.__cause__.__cause__, NotImplementedError):
                pytest.xfail('Podman is too old')
            raise
        async with layout.net_pod.container.machine.machine_running():
            await layout.net_pod.container.machine.container_exec('apt', 'update')
            machine = layout.net_pod.container.machine
            await machine.container_exec('apt', '-y', 'install', 'iproute2')
            result = await machine.container_exec('ip', 'addr', 'show')
            address =machine.network_links['eth0'].merged_v4_config.address
            assert address
            assert str(address) in str(result.stdout, 'utf-8')
    finally:
        try: await layout.net_pod.pod.delete(force=True)
        except Exception: pass

@async_test
async def test_podman_image_model(layout_fixture):
    await layout_fixture.ImageModelCustomizations.build_image()

@async_test
async def test_podman_volume(layout_fixture):
    layout = layout_fixture
    ainjector = layout.ainjector
    machine = None
    
    await layout.volume.async_become_ready()
    try:
        try:
            async with layout.volume.filesystem_access() as path:
                (path/'foo').write_text('bar')
        except  carthage.sh.ErrorReturnCode_125 as e:
            if b'unrecognized' in e.stderr:
                pytest.xfail('podman too old')
            raise
        machine = await ainjector.get_instance_async(InjectionKey(Machine, host='populated-volume-container'))
        async with machine.machine_running():
            await machine.run_command('cat', '/volume/foo')
            
    finally:
        await layout.volume.delete()
        if machine:
            await machine.delete()
        try:
            await layout.populated_volume.delete()
        except Exception:
            logger.exception('deleting populated volume')
            
        
