# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, logging, os, os.path, shutil
import mako, mako.lookup, mako.template
from .dependency_injection import *
from .utils import when_needed, memoproperty
from .image import SetupTaskMixin, setup_task, ImageVolume
from .machine import Machine, SshMixin
from . import sh
from .config import ConfigLayout
import carthage.network

logger = logging.getLogger('carthage.vm')

_resources_path = os.path.join(os.path.dirname(__file__), "resources")
_templates = mako.lookup.TemplateLookup([_resources_path+'/templates'])


vm_image = InjectionKey('vm-image')

# Our capitalization rules are kind of under-sspecified.  We're not
# upcasing all letters of acronyms in camel-case compounds, but Vm
# seems strange.  VM is canonical but Vm is an accepted alias.
@inject(
    config_layout = ConfigLayout,
    injector = Injector,
    image = vm_image,
    network_config = carthage.network.NetworkConfig
    )
class VM(Machine, SetupTaskMixin):

    def __init__(self, name, console_needed = False,
                 *, injector, config_layout, image, network_config):
        super().__init__(injector = injector, config_layout = config_layout,
                         name = name)
        self.network_config_unresolved = network_config
        self.image = image
        self.console_needed = console_needed
        self.running = False
        self.volume = None
        self.network_config = None
        self.vm_running = self.machine_running
        self._operation_lock = asyncio.Lock()

    

    def gen_volume(self):
        if self.volume is not None: return
        self.volume = self.image.clone_for_vm(self.name)
        self.ssh_rekeyed()
        os.makedirs(self.stamp_path, exist_ok = True)


    async def write_config(self):
        template = _templates.get_template("vm-config.mako")
        if self.network_config is None:
            self.network_config = await self.ainjector(self.network_config_unresolved.resolve)
        self.gen_volume()
        with open(self.config_path, 'wt') as f:
            f.write(template.render(
                console_needed = self.console_needed,
                name =self.full_name,
                network_config = self.network_config,
                volume = self.volume))

                    

    @memoproperty
    def config_path(self):
        return os.path.join(self.config_layout.vm_image_dir, self.name+'.xml')

    
    async def start_vm(self):
        async with self._operation_lock:
            if self.running is True: return
            await self.start_dependencies()
            await self.write_config()
            await sh.virsh('create',
                      self.config_path,
                      _bg = True, _bg_exc = False)
            self.running = True

    start_machine = start_vm
    
    async def stop_vm(self):
        async with self._operation_lock:
            if not self.running:
                raise RuntimeError("VM is not running")
            await sh.virsh("shutdown", self.full_name,
                       _bg = True,
                       _bg_exc = False)
            for i in range(10):
                await asyncio.sleep(5)
                try: sh.virsh('domid', self.full_name)
                except sh.ErrorReturnCode_1:
                    #it's shut down
                    self.running = False
                    break
            if self.running:
                try:
                    sh.virsh('destroy', self.full_name)
                except sh.ErrorReturnCode: pass
                self.running = False

    stop_machine = stop_vm
    
            
    def close(self):
        if self.running:
            sh.virsh("destroy", self.full_name)
            self.running = False
        self.volume.close()
        try: os.unlink(self.config_path)
        except FileNotFoundError: pass
        if self.config_layout.delete_volumes:
            try: shutil.rmtree(self.stamp_path)
            except FileNotFoundError: pass

    def __del__(self):
        self.close()

    

    async def async_ready(self):
        await self.write_config()
        await self.run_setup_tasks(context = self.machine_running)
        return self

    @property
    def stamp_path(self):
        return self.volume.path+'.stamps'
