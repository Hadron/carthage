# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, os, shutil, sys
from ..image import ContainerImage, setup_task, SetupTaskMixin, ImageVolume, ContainerImageMount
from ..container import Container, container_volume, container_image
from ..dependency_injection import inject, Injector, AsyncInjectable, AsyncInjector
from ..config import ConfigLayout
from .. import sh
from ..utils import when_needed
import carthage.ssh
import carthage.network

class HadronImageMixin(SetupTaskMixin):

    @setup_task('hadron_packages')
    async def setup_hadron_packages(self):
        ainjector = self.injector(AsyncInjector)
        ainjector.add_provider(container_volume, self)
        ainjector.add_provider(container_image, self)
        container = await ainjector(Container, name = self.name,
                                    skip_ssh_keygen = True)
        try:
            bind_mount = '--bind-ro='+self.config_layout.hadron_operations+":/hadron-operations"
            process = await container.run_container('/bin/systemctl', 'disable', 'sddm')
            await process
            process = await container.run_container(bind_mount, "/usr/bin/apt",
                                                    "install", "-y", "ansible",
                                                    "git", "python3-pytest",
            )
            await process
            process = await container.run_container(bind_mount, "/usr/bin/ansible-playbook",
                                                    "-clocal",
                                                    "-ehadron_os=ACES",
                                                    "-ehadron_track=proposed",
                                                    "-epackagedir=/hadron-operations/ansible/packages",
                                                    "-ehadron_release=unstable",
                                                    "-eaces_apt_server=apt-server.aces-aoe.net",
                                                    "-i/hadron-operations/ansible/localhost-debian.txt",
                                                    "/hadron-operations/ansible/commands/hadron-packages.yml"
            )
            await process
            process = await container.run_container("/usr/bin/apt", "update")
            await process
        finally: pass
        
    @setup_task('ssh_authorized_keys')
    @inject(authorized_keys = carthage.ssh.AuthorizedKeysFile)
    def add_authorized_keys(self, authorized_keys):
        os.makedirs(os.path.join(self.path, "root/.ssh"), exist_ok = True)
        shutil.copy2(authorized_keys.path,
                     os.path.join(self.path, 'root/.ssh/authorized_keys'))

@inject(
    config_layout = ConfigLayout,
    injector = Injector
    )
class HadronContainerImage(ContainerImage, HadronImageMixin):

    def __init__(self, injector, config_layout):
        super().__init__(config_layout = config_layout, name = "base-hadron")
        self.injector = injector
        
        
        
@inject(
    config_layout = ConfigLayout,
    injector = Injector,
    loop = asyncio.AbstractEventLoop,
    image = container_image,
    network_config = carthage.network.NetworkConfig)
class TestDatabase(Container):

    def __init__(self, name = "test-database", **kwargs):
        super().__init__(name = name, **kwargs)
        

    @setup_task("install-db")
    async def install_packages(self):
        with open(self.volume.path+"/etc/network/interfaces", "wt+") as f:
            #Convince NetworkManager to leave eth1 alone before internet-zone comes along
            f.write("iface eth1 inet manual\n")
        async with self.container_running:
            await self.network_online()
            await self.shell("/usr/bin/apt",
                                               "-y", "install", "hadron-inventory-admin",
                                           "hadron-photon-admin",
                             "socat",
                             "hadron-ansible",
                             _in = "/dev/null",
                             _out = self._out_cb,
                             _err_to_out = True,
                             _bg = True, _bg_exc = False)

    @inject(ssh_key = carthage.ssh.SshKey)
    @setup_task('clone-hadron-ops')
    async def clone_hadron_operations(self, ssh_key):
        await sh.git('bundle',
                     'create', self.volume.path+"/hadron-operations.bundle",
                     "HEAD",
                     "master",
                     _bg = True, _bg_exc = False,
                     _cwd = self.config_layout.hadron_operations)
        process = await self.run_container('/usr/bin/git',
                                     'clone', '--branch=master',
                                     '/hadron-operations.bundle')
        await process
        hadron_ops = os.path.join(self.volume.path, "hadron-operations")
        authorized_keys = os.path.join(hadron_ops, "hadron/inventory/config/templates/authorized_keys.mako")
        with open(authorized_keys + ".new", "wt") as f:
            f.write("#Carthage Automation Key\n")
            f.write(ssh_key.pubkey_contents)
            with open(authorized_keys, "rt") as f_in:
                f.write(f_in.read())
        os.rename(authorized_keys+".new", authorized_keys)
        os.unlink(os.path.join(self.volume.path, 'hadron-operations.bundle'))
        
    @setup_task('copy-database')
    async def copy_database_from_master(self):
        "Copy the master database.  Run automatically.  Could be run agains if hadroninventoryadmin is locally dropped and recreated"
        async with self.container_running:
            await self.network_online()
            await self.shell('/usr/bin/python3',
                         '-mhadron.inventory.config.update',
                         '--copy=postgresql:///hadroninventoryadmin',
                         '--copy-users',
                         _bg = True,
                         _bg_exc = False,
                             _out = self._out_cb,
                         _err_to_out = True)
        

    @setup_task('make-update')
    async def make_update(self):
        "Run make update in /hadron-operations; can be repeated as needed"
        async with self.container_running:
            await self.network_online()
            await self.shell('/bin/sh', '-c',
                             "cd /hadron-operations&&make update",
                       _out = self._out_cb,
                       _err_to_out = True,
                       _bg = True,
                       _bg_exc = False)
            await self.shell("/bin/sh", "-c",
                             "cd /hadron-operations/ansible&&ansible-playbook -c local commands/test-database.yml",
                             _bg = True, _bg_exc = False,
                             _out = self._out_cb,
                             _err_to_out = True)

    ip_address = "192.168.101.1"
    

hadron_container_image = when_needed(HadronContainerImage)

@inject(config_layout = ConfigLayout,
        ainjector = AsyncInjector)
class HadronContainerImageMount(ContainerImageMount, HadronImageMixin): pass


@inject(
    config_layout = ConfigLayout,
    ainjector = AsyncInjector
    )
class HadronVmImage(ImageVolume):

    def __init__(self, *, config_layout, ainjector):
        super().__init__("base-hadron",
                         path = config_layout.vm_image_dir+'/base-hadron.raw',
                         create_size = config_layout.vm_image_size,
                         config_layout = config_layout,
                         ainjector = ainjector)
    

    
    @setup_task('hadron-customizations')
    async def customize_for_hadron(self):
        mount = await self.ainjector(HadronContainerImageMount, self)
#        mount.close()
        
        
