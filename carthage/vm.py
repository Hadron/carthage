# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, json, logging, os, os.path, shutil
import mako, mako.lookup, mako.template
from .dependency_injection import *
from .utils import when_needed, memoproperty
from .image import SetupTaskMixin, setup_task, ImageVolume
from .machine import Machine, SshMixin, ContainerCustomization
from . import sh
from .config import ConfigLayout
from .ports import PortReservation
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
        self.image = image
        self.console_needed = console_needed
        if self.console_needed:
            self.console_port = injector(PortReservation)
        self.running = False
        self.volume = None
        self.vm_running = self.machine_running
        self._operation_lock = asyncio.Lock()



    def gen_volume(self):
        if self.volume is not None: return
        self.volume = self.image.clone_for_vm(self.name)
        self.ssh_rekeyed()
        os.makedirs(self.stamp_path, exist_ok = True)


    async def write_config(self):
        template = _templates.get_template("vm-config.mako")
        await self.resolve_networking()
        for i, link in self.network_links.items():
            await link.instantiate(carthage.network.BridgeNetwork)
            await self.image.async_become_ready()
        self.gen_volume()
        with open(self.config_path, 'wt') as f:
            f.write(template.render(
                console_needed = self.console_needed,
                console_port = self.console_port.port if self.console_needed else None,
                name =self.full_name,
                links = self.network_links,
                if_name = lambda n: carthage.network.base.if_name("vn", self.config_layout.container_prefix, n.name, self.name),
                volume = self.volume))
            if self.console_needed:
                with open(self.console_json_path, "wt") as f:
                    f.write(self._console_json())


    @memoproperty
    def config_path(self):
        return os.path.join(self.config_layout.vm_image_dir, self.name+'.xml')

    @memoproperty
    def console_json_path(self):
        return os.path.join(self.config_layout.vm_image_dir, self.name+'.console')


    async def start_vm(self):
        async with self._operation_lock:
            if self.running is True: return
            await self.start_dependencies()
            await self.write_config()
            await sh.virsh('create',
                      self.config_path,
                      _bg = True, _bg_exc = False)
            if self.__class__.ip_address is Machine.ip_address:
                try:
                    await self._find_ip_address()
                except:
                    sh.virsh("destroy", self.full_name,
                             _bg = True, _bg_exc = False)
                    raise
            self.running = True

    start_machine = start_vm

    async def stop_vm(self):
        async with self._operation_lock:
            if not self.running:
                return
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


    def close(self, canceled_futures = None):
        if self.running:
            try:
                sh.virsh("destroy", self.full_name)
                self.running = False
            except Exception: pass
        self.volume.close()
        try: os.unlink(self.config_path)
        except FileNotFoundError: pass
        if self.config_layout.delete_volumes:
            try: shutil.rmtree(self.stamp_path)
            except FileNotFoundError: pass
        self.injector.close(canceled_futures = canceled_futures)

    def __del__(self):
        self.close()



    async def async_ready(self):
        await self.write_config()
        await self.run_setup_tasks(context = self.machine_running(ssh_online = True))
        return await super().async_ready()

    async def _find_ip_address(self):
        for i in range(30):
            try:
                res = await sh.virsh("qemu-agent-command",
                                     self.full_name,
                                     '{"execute":"guest-network-get-interfaces"}',
                                     _bg = True, _bg_exc = False, _timeout = 5)
            except sh.ErrorReturnCode_1 as e:
                # We should retry in a bit if the message contains 'not connected' and fail for other errors
                if b'connected' not in e.stderr: raise
                await asyncio.sleep(3)
                continue

            js_res = json.loads(res.stdout)
            for item in js_res['return']:
                if item['name'] == 'lo': continue
                if 'ip-addresses' not in item: continue
                for addr in item['ip-addresses']:
                    if addr['ip-address'].startswith('fe80'): continue
                    self.ip_address = addr['ip-address']
                    return
            await asyncio.sleep(3)

    @property
    def stamp_path(self):
        return self.volume.path+'.stamps'

    def _console_json(self):

        d = {
            "password": "aces",
            "user": "aces",
            "port": self.console_port.port,
            "host": sh.hostname('--fqdn', _encoding = 'utf-8').strip(),
            "description": self.full_name,
            "type": "spice",
            "ca": self.vm_ca(),
        }

        return json.dumps(d)

    @classmethod
    def vm_ca(cls):
        paths = ('/etc/pki/libvirt-spice', '/etc/pki/qemu')
        for p in paths:
            ca_file = os.path.join(p,'ca-cert.pem')
            if os.path.exists(ca_file):
                with open(ca_file, 'rt') as f:
                    ca = f.read()
                ca = ca.replace("\n", "\\n")
                ca = ca.replace("\r", "")
                return ca
        raise FileNotFoundError

Vm = VM
    

class InstallQemuAgent(ContainerCustomization):

    description = "Install qemu guest agent"

    @setup_task("Install qemu guest agent")
    async def install_guest_agent(self):
        await self.container_command("/usr/bin/apt", "-y", "install", "qemu-guest-agent")

        __all__ = ('VM', 'Vm', 'InstallQemuAgent')
        
