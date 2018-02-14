# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, os, sys
from .image import ImageVolume, setup_task
from .container import Container, container_volume, container_image
from .dependency_injection import inject, Injector, AsyncInjectable, AsyncInjector
from .config import ConfigLayout
from . import sh

@inject(
    config_layout = ConfigLayout,
    injector = Injector
    )
class HadronImageVolume(ImageVolume):

    def __init__(self, injector, config_layout):
        super().__init__(config_layout = config_layout, name = "base-hadron")
        self.injector = injector
        
    @setup_task('hadron_packages')
    async def setup_hadron_packages(self):
        ainjector = self.injector(AsyncInjector)
        ainjector.add_provider(container_volume, self)
        ainjector.add_provider(container_image, self)
        container = await ainjector(Container, name = self.name)
        try:
            bind_mount = '--bind-ro='+self.config_layout.hadron_operations+":/hadron-operations"
            process = container.run_container('/bin/systemctl', 'disable', 'sddm')
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
                                  "-ehadron_release=unstable",
                                  "-eaces_apt_server=apt-server.aces-aoe.net",
                                  "-i/hadron-operations/ansible/localhost-debian.txt",
                                  "/hadron-operations/ansible/commands/hadron-packages.yml"
                                          )
            await process
            process = await container.run_container("/usr/bin/apt", "update")
            await process
        finally: pass

@inject(
    config_layout = ConfigLayout,
    injector = Injector,
    loop = asyncio.AbstractEventLoop,
    image = container_image)
class TestDatabase(Container):

    def __init__(self, name = "test-database", **kwargs):
        super().__init__(name = name, **kwargs)
        

    @setup_task("install-db")
    async def install_packages(self):
        async with self.container_running:
            await self.network_online()
            await self.shell("/usr/bin/apt",
                                               "-y", "install", "hadron-inventory-admin",
                                           "hadron-photon-admin",
                             "hadron-ansible",
                             _in = "/dev/null",
                             _out = self._out_cb,
                             _err_to_out = True,
                             _bg = True, _bg_exc = False)

    @setup_task('clone-hadron-ops')
    async def clone_hadron_operations(self):
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
        os.unlink(os.path.join(self.volume.path, 'hadron-operations.bundle'))
        
