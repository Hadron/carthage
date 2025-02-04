# Copyright (C) 2018, 2019, 2020, 2021, 2024, 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import json
import logging
import os
import os.path
import shutil
import types
import uuid
import xml.etree.ElementTree
import mako
import mako.lookup
import mako.template
from pathlib import Path
from .dependency_injection import *
from . import deployment
from .utils import when_needed, memoproperty
from .setup_tasks import SetupTaskMixin, setup_task
from .image import  ImageVolume
from .machine import Machine, SshMixin, ContainerCustomization, disk_config_from_model, AbstractMachineModel
from . import sh
from .config import ConfigLayout
from .ports import PortReservation
import carthage.network

logger = logging.getLogger('carthage.vm')

_resources_path = os.path.join(os.path.dirname(__file__), "resources")
_templates = mako.lookup.TemplateLookup([_resources_path + '/templates'])


vm_image_key = InjectionKey('vm-image')

#dataclasses.dataclass
class VirtiofsMount(Injectable):

    destination: str
    source:str
    readonly:bool = False
    priority:int = 100

    def __init__(self, *, destination, source, readonly=None, priority=None, **kwargs):
        self.destination = destination
        self.source = source
        if readonly is not None: self.readonly = readonly
        if priority is not None: self.priority = priority
        super().__init__(**kwargs)
        
    def default_instance_injection_key(self):
        return InjectionKey(VirtiofsMount, destination=self.destination, priority=self.priority)

    

@inject_autokwargs(
    injector=Injector,
    image=InjectionKey(vm_image_key, _defer=True),
    network_config=carthage.network.NetworkConfig
)
class Vm(Machine, SetupTaskMixin):

    '''
    A libvirt VM implementation.
    '''

    # For now BridgeNetwork is not really a deployable
    # network_implementation_class = carthage.network.BridgeNetwork

    def __init__(self, name, *, console_needed=None,
                 **kwargs):
        super().__init__(name=name, **kwargs)
        self.config_layout = self.injector(ConfigLayout)
        injector = self.injector
        config_layout = self.config_layout
        self.console_needed = console_needed
        if self.console_needed:
            self.console_port = injector(PortReservation)
        self.running = False
        self.closed = False
        self.volume = None
        self.vm_running = self.machine_running
        self._operation_lock = asyncio.Lock()

    @memoproperty
    def uuid(self):
        from .modeling import CarthageLayout
        layout = self.injector.get_instance(InjectionKey(CarthageLayout, _optional=True))
        if layout:
            layout_uuid = layout.layout_uuid
            return uuid.uuid5(layout_uuid, 'vm:'+self.full_name)
        return uuid.uuid4()


    async def gen_volume(self):
        if self.volume is not None:
            return
        with instantiation_not_ready():
            self.volume = await self.ainjector(
                ImageVolume,
                name=self.name,
                base_image=self.image,
                size=self.config_layout.vm_image_size)
            await self.volume.find()
        self.ssh_rekeyed()
        os.makedirs(self.stamp_path, exist_ok=True)

    async def find(self):
        await self.gen_volume()
        if self.domid():
            return True
        if self.volume:
            return await self.volume.find()
        return False

    async def find_or_create(self):
        if await self.find():
            return
        await self.start_machine()

    async def write_config(self):
        from .modeling import CarthageLayout
        template = _templates.get_template("vm-config.mako")
        await self.resolve_networking()
        for i, link in self.network_links.items():
            await link.instantiate(carthage.network.BridgeNetwork)
        await self.gen_volume()
        layout = await self.ainjector.get_instance_async(InjectionKey(CarthageLayout, _ready=False, _optional=True))
        if layout:
            layout_name = layout.layout_name
            try:
                orphan_policy = self.injector.get_instance(deployment.orphan_policy)
            except KeyError:
                orphan_policy = deployment.DeletionPolicy.delete
        else:
            layout_name = ''
            orphan_policy  = deployment.DeletionPolicy.retain

        ci_data = None
        if self.model and getattr(self.model, 'cloud_init', False):
            ci_data = await self.ainjector(carthage.cloud_init.generate_cloud_init_cidata)
        disk_config = []
        async for d in await self.ainjector(qemu_disk_config, self, ci_data):
            disk_config.append(types.SimpleNamespace(**d))
        console_needed = self.console_needed
        if console_needed is None:
            console_needed = getattr(self.model, 'console_needed', False)
        with open(self.config_path, 'wt') as f:
            f.write(template.render(
                console_needed=console_needed,
                console_port=self.console_port.port if self.console_needed else None,
                name=self.full_name,
                layout_name=layout_name,
                orphan_policy=orphan_policy,
                links=self.network_links,
                model_in=self.model,
                disk_config=disk_config,
                virtiofs_mounts=self.virtiofs_mounts,
                if_name=lambda n: carthage.network.base.if_name(
                    "vn", self.config_layout.container_prefix, n.name, self.name),
                uuid=self.uuid,
                volume=self.volume))
            if self.console_needed:
                with open(self.console_json_path, "wt") as f:
                    f.write(self._console_json())

    @memoproperty
    def config_path(self):
        return os.path.join(self.config_layout.vm_image_dir, self.name + '.xml')

    @memoproperty
    def console_json_path(self):
        return os.path.join(self.config_layout.vm_image_dir, self.name + '.console')

    async def start_vm(self):
        async with self._operation_lock:
            if self.running is True:
                return
            await self.start_dependencies()
            await super().start_machine()
            await self.write_config()
            await sh.virsh('create',
                           self.config_path,
                           _bg=True, _bg_exc=False)
            if self.__class__.ip_address is Machine.ip_address:
                try:
                    self.ip_address
                except NotImplementedError:
                    try:
                        await self._find_ip_address()
                    except Exception as e:
                        sh.virsh("destroy", self.full_name,
                                 _bg=True, _bg_exc=False)
                        raise e from None
            self.running = True

    start_machine = start_vm

    def domid(self):
        try:
            bdomid =sh.virsh('domid', self.full_name, _bg=False).stdout
            domid = str(bdomid, 'utf-8').strip()
        except sh.ErrorReturnCode_1:
            return None
        return domid

    async def stop_vm(self):
        async with self._operation_lock:
            if not self.running:
                return

            await sh.virsh("shutdown", self.full_name,
                           _bg=True,
                           _bg_exc=False)
            for i in range(10):
                await asyncio.sleep(5)
                if not await self.is_machine_running(find_ip_address=False):
                    break
            if self.running:
                try:
                    sh.virsh('destroy', self.full_name, _bg=False)
                except sh.ErrorReturnCode:
                    pass
                self.running = False
            await super().stop_machine()

    stop_machine = stop_vm

    def close(self, canceled_futures=None):
        if self.closed:
            return
        if (not self.config_layout.persist_local_networking) or self.config_layout.delete_volumes:
            if self.running:
                try:
                    sh.virsh("destroy", self.full_name, _bg=False)
                    self.running = False
                except Exception:
                    pass
            try:
                os.unlink(self.config_path)
            except FileNotFoundError:
                pass
        if self.config_layout.delete_volumes:
            try:
                shutil.rmtree(self.stamp_path)
            except FileNotFoundError:
                pass
        if self.volume:
            self.volume.close()
        self.injector.close(canceled_futures=canceled_futures)
        self.closed = True

    def __del__(self):
        self.close()

    async def async_ready(self):
        await self.write_config()
        await self.is_machine_running()

        await self.run_setup_tasks(context=self.machine_running(ssh_online=True))
        return await super().async_ready()

    async def is_machine_running(self, find_ip_address:bool=True):
        domid = self.domid()
        if domid and domid != '-':
            self.running = True
        else:
            self.running = False
        if self.running and find_ip_address and (self.__class__.ip_address is Machine.ip_address):
            try:
                self.ip_address
            except NotImplementedError:
                await self._find_ip_address()
        return self.running

    async def wait_for_shutdown(self, timeout=30*60):
        '''
        Wait for up to timeout seconds for the machine to shut down.
        '''
        time_remaining = timeout
        while await self.is_machine_running():
            await asyncio.sleep(5)
            time_remaining -= 5
            if time_remaining <= 0:
                raise TimeoutError


    async def _find_ip_address(self):
        for i in range(30):
            try:
                res = sh.virsh("qemu-agent-command",
                                     self.full_name,
                                     '{"execute":"guest-network-get-interfaces"}',
                                     _bg=True, _bg_exc=False, _timeout=5)
                await res
            except sh.TimeoutException:
                await asyncio.sleep(3)
            except sh.ErrorReturnCode_1 as e:
                # We should retry in a bit if the message contains 'not connected' and fail for other errors
                if b'connected' not in e.stderr:
                    raise
                await asyncio.sleep(5)
                continue

            js_res = json.loads(res.stdout)
            for item in js_res['return']:
                if item['name'] == 'lo':
                    continue
                if 'ip-addresses' not in item:
                    continue
                for addr in item['ip-addresses']:
                    if addr['ip-address'].startswith('fe80'):
                        continue
                    elif addr['ip-address'].startswith('169.25'):
                        continue
                    elif addr['ip-address'].startswith('::'):
                        continue
                    elif addr['ip-address'].startswith('127.'):
                        continue
                    self.ip_address = addr['ip-address']
                    return
            await asyncio.sleep(3)

    @memoproperty
    def stamp_subdir(self):
        if self.volume:
            return self.volume.stamp_subdir
        return 'libvirt/'+self.name

    async def dynamic_dependencies(self):
        await self.gen_volume()
        results = await super().dynamic_dependencies()
        if self.volume:
            results.append(self.volume)
        disk_config = disk_config_from_model(getattr(self, 'model', {}),
                                         default_disk_config=[
                                             dict(),
                                             dict(target_type='cdrom',
                                                  source_type='file',
                                                  driver='raw',
                                                  qemu_source='file',
                                                  readonly=True)],
                                             )
        disk_config = await resolve_deferred(self.ainjector, disk_config, {})
        for entry in disk_config:
            if 'volume' in entry:
                results.append(entry['volume'])
        return results

    def _console_json(self):

        d = {
            "password": "aces",
            "user": "aces",
            "port": self.console_port.port,
            "host": sh.hostname('--fqdn', _encoding='utf-8', _bg=False).strip(),
            "description": self.full_name,
            "type": "spice",
            "ca": self.vm_ca(),
        }

        return json.dumps(d)

    @classmethod
    def vm_ca(cls):
        paths = ('/etc/pki/libvirt-spice', '/etc/pki/qemu')
        for p in paths:
            ca_file = os.path.join(p, 'ca-cert.pem')
            if os.path.exists(ca_file):
                with open(ca_file, 'rt') as f:
                    ca = f.read()
                ca = ca.replace("\n", "\\n")
                ca = ca.replace("\r", "")
                return ca
        raise FileNotFoundError

    async def delete(self):
        await self.is_machine_running(find_ip_address=False)
        if self.running:
            await self.stop_machine()
        await self.gen_volume()
        if self.volume:
            await self.volume.delete()

        try:
            shutil.rmtree(self.stamp_path)
        except Exception: pass
        del self.stamp_path

    @memoproperty
    def virtiofs_mounts(self):
        '''
        Return a list of VirtiofsMounts that should be mounted by this VM in config order. By default self.injector.filter_instantiate is used.
        '''
        res =  [x[1] for x in self.injector.filter_instantiate(VirtiofsMount, ['destination'])]
        res.sort(key=lambda k:k.priority)
        return res
    
VM = Vm


class InstallQemuAgent(ContainerCustomization):

    description = "Install qemu guest agent"

    @setup_task("Install qemu guest agent")
    async def install_guest_agent(self):
        await self.container_command("/usr/bin/apt", "-y", "install", "qemu-guest-agent")


@inject(ainjector=AsyncInjector)
async def qemu_disk_config(vm, ci_data, *, ainjector):
    # Handle qemu specific disk_config
    disk_config = disk_config_from_model(getattr(vm, 'model', {}),
                                         default_disk_config=[
                                             dict(),
                                             dict(target_type='cdrom',
                                                  source_type='file',
                                                  driver='raw',
                                                  qemu_source='file',
                                                  readonly=True)],
                                         )
    # Unless a volume explicitly requests not ready, we bring it to ready.
    with instantiation_not_ready(ready=True):
        for i, entry in enumerate(disk_config):
            if i == 0:  # primary disk
                if 'volume' not in entry:
                    entry['volume'] = vm.volume
                if 'size' not in entry:
                    entry['size'] = vm.config_layout.vm_image_size
            if 'cache' not in entry:
                try:
                    entry['cache'] = vm.model.disk_cache
                except AttributeError:
                    entry['cache'] = 'writeback'
            if 'volume' not in entry and 'size' in entry:
                entry['volume'] = await ainjector(
                    ImageVolume, name=vm.name + f'_disk_{i}',
                    size=entry['size'],
                    )
            if 'volume' in entry and isinstance(entry['volume'], InjectionKey):
                entry['volume'] = await vm.ainjector.get_instance_async(entry['volume'])
            elif 'volume' in entry:
                await entry['volume'].async_become_ready()
            if 'size' in entry and 'volume' in entry:
                await entry['volume'].resize(entry['size'])
            entry.setdefault('target_type', 'disk')
            if 'volume' in entry:
                entry.update(entry['volume'].qemu_config(entry))
            entry.setdefault('source_type', 'file')
            entry.setdefault('driver', 'raw')
            entry.setdefault('qemu_source', 'dev' if entry['source_type'] == 'block' else 'file')
            entry.setdefault('bus', 'scsi')
            yield entry
    if ci_data:
        yield dict(
            target_type='cdrom',
            source_type='file',
            qemu_source='file',
            driver='raw',
            readonly=True,
            path=ci_data,
            bus='sata',
            cache='writeback')

@inject(base_image=None)
class LibvirtCreatedImage(ImageVolume):

    '''
    Represents an image created by booting a VM, often with CDs attached and running some operations. The resulting primary disk is used as the image.
    The VM is created only if the image is not available.

This class is almost always subclassed.  The following are expected to be overwridden:

    vm_customizations
        A set of customizations to apply to the Vm while it is running.
    '''

    disk_config: list[dict] = [{}]

    #: From AbstractMachineModel
    override_dependencies: bool = False
    qemu_agent_probe:bool = False #:If True, do not override ip_address and let the qemu agent probe run
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args,
                         populate=None,
                         **kwargs)
        if not self.qemu_agent_probe:
            try:
                self.ip_address
            except AttributeError:
                self.ip_address = NotImplemented
                
        self.injector.add_provider(InjectionKey(AbstractMachineModel), dependency_quote(self))


    async def _prepare_vm(self):
        '''
        Prepare the vm for the image creation.
        '''
        machine_type = getattr(self, 'machine_type', Vm)
        disk_config = [dict(d) for d in self.disk_config]

        with instantiation_not_ready():
            self.vm = await self.ainjector(
                machine_type, name=self.name,
                image=None)
            self.vm.network_links = self.network_links = {}
            self.vm.volume = self
            self.vm.model = self


    async def _build_image(self):
        '''
        Prepare the vm and build the image; called from rebuild_image and unpack
        '''
        if not self.path.exists():
            self._do_create_volume()
        try:
            await self._prepare_vm()
            await self.vm.start_machine()
            async with self.vm.machine_running(ssh_online=False):
                await self.vm.async_become_ready()
                for c in self.vm_customizations:
                    await self.vm.apply_customization(c)
        except Exception:
            logger.info('Shutting down image creation for %s because of error', self.name)
            await self.delete()
            raise
        finally:
            await self.vm.is_machine_running()
            await self.vm.stop_machine()

    async def populate(self):
        '''
        Called from the SetupTask to build the image.
        '''
        await self._build_image()

class LibvirtDeployableFinder(carthage.deployment.DeployableFinder):

    name = 'libvirt'

    async def find(self, ainjector):
        '''
        MachineDeployableFinder already finds Vms.
        '''
        return []

    async def find_orphans(self, deployables):
        try:
            import libvirt
            import carthage.modeling
        except ImportError:
            logger.debug('Not looking for libvirt orphans because libvirt API is not available')
            return []
        con = libvirt.open('')
        vm_names = [v.full_name for v in deployables if isinstance(v, Vm)]
        try:
            layout = await self.ainjector.get_instance_async(carthage.modeling.CarthageLayout)
            layout_name = layout.layout_name
        except KeyError:
            layout_name = None
        if layout_name is None:
            logger.info('Unable to find libvirt orphans because layout name not set')
            return []
        results = []
        for d in con.listAllDomains():
            try:
                metadata_str = d.metadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT, 'https://github.com/hadron/carthage')
            except libvirt.libvirtError: continue
            metadata = xml.etree.ElementTree.fromstring(metadata_str)
            if metadata.attrib['layout'] != layout_name: continue
            if d.name() in vm_names:
                continue
            with instantiation_not_ready():
                vm = await self.ainjector(
                    Vm,
                    name=d.name(),
                    image=None,
                    )
                vm.injector.add_provider(deployment.orphan_policy, deployment.DeletionPolicy[metadata.attrib['orphan_policy']])
            if await vm.find():
                results.append(vm)
        return results

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.injector.add_provider(ConfigLayout)
        cl = self.injector.get_instance(ConfigLayout)
        cl.container_prefix = ""

def vm_as_image(key):
    '''
    Return the volume of a VM to be used for cloning.  Typical usage::

      add_provider(vm_image_key, vm_as_image(InjectionKey(Machine, host='host_to_clone'), allow_multiple=True)

    Allow_multiple is recommended simply to make sure that the VM is stopped every time it is used as a clone base.
    '''
    @inject(vm=InjectionKey(key, _ready=True))
    async def image_volume(vm):
        if isinstance(vm, AbstractMachineModel):
            ainjector = vm.injector(AsyncInjector)
            vm = await ainjector.get_instance_async(Machine)
        await vm.async_become_ready()
        await vm.stop_machine()
        return vm.volume
    return image_volume

__all__ = ('VM', 'Vm', 'vm_as_image', 'InstallQemuAgent', 'VirtiofsMount')
