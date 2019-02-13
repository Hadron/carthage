# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, logging, time
import carthage.ansible, carthage.network
import os.path
from ..machine import Machine
from ..dependency_injection import *
from ..config import ConfigLayout, config_defaults, config_key
from ..setup_tasks import SetupTaskMixin, setup_task, SkipSetupTask
from ..utils import memoproperty
from .image import VmwareDataStore, VmdkTemplate
from .inventory import VmwareStampable, VmwareConnection, VmwareFolder
from . import network

logger = logging.getLogger('carthage.vmware')

config_defaults.add_config({
    'vmware': {
        'datacenter': None,
        'folder': 'carthage',
        'cluster': None,
        'hardware': {
            'boot_firmware': 'efi',
            'version': 14,
            'memory': 4096,
            'disk': 25,
            'cpus': 1,
            'paravirt': True,
            },
        }})
vmware_config = config_key('vmware')

@inject(config_layout = ConfigLayout,
        injector = Injector,
        connection = VmwareConnection)
class VmFolder(VmwareFolder):
    kind = 'vm'

    async def delete(self):
        v = None
        try:
            v = self.injector(inventory.VmInventory, folder = self)
            cluster_name = self.config_layout.vmware.cluster
            datacenter_name = self.config_layout.vmware.datacenter
            cluster = self.connection.content.searchIndex.FindByInventoryPath(f"{datacenter_name}/host/{cluster_name}")
            if not cluster:
                raise RuntimeError(f"Cluster {cluster_name} not found")
            rp = cluster.resourcePool
            for vm in v.view.view:
                try: vm.MarkAsVirtualMachine(rp)
                except:
                    pass
                try: vm.PowerOff()
                except: pass
        finally:
            if v: v.close()
        await asyncio.sleep(2)
        return await inventory.wait_for_task(self.inventory_object.Destroy())
        
            

    
@inject(config = vmware_config)
def vmware_dict(config, **kws):
    '''
:returns: A dictionary containing vmware common parameters to pass into Ansible
'''
    d = dict(
        datacenter = config.datacenter,
        username = config.username,
        hostname = config.hostname,
        validate_certs = config.validate_certs,
        password = config.password)
    d.update(kws)
    return d

    
@inject(
    config = ConfigLayout,
    injector = Injector,
    storage = VmwareDataStore,
    folder = InjectionKey(VmFolder, optional = True),
    network_config = InjectionKey(carthage.network.NetworkConfig, optional = True),
    # We add a template VM to the dependencies later avoiding a forward reference
    )
class Vm(Machine, VmwareStampable):

    stamp_type = "vm"

    nested_virt = False #: Enable nested virtualization
    def __init__(self, name, template,
                 guest_id = "ubuntu64Guest",
                 *, injector, config, storage, folder, network_config = None):
        super().__init__(name, injector, config)
        self.storage = storage
        self.running = False
        self.folder = folder
        vm_config = config.vmware
        self.cpus = vm_config.hardware.cpus
        self.memory = vm_config.hardware.memory
        self.paravirt = vm_config.hardware.paravirt
        self.disk_size = vm_config.hardware.disk
        if config.vm_image_size > self.disk_size*1000000000:
            self.disk_size = int(config.vm_image_size/1000000000)
        self.guest_id = guest_id
        self.network_config = network_config
        self.template_name = None
        self.template_snapshot = None
        self.inventory_object = None
        if template:
            self.template_name = template.inventory_object.name
            if template.clone_from_snapshot:
                self.template_snapshot = template.clone_from_snapshot
        self._operation_lock = asyncio.Lock()

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.name}>"
    
    async def async_ready(self):
        if not self.folder:
            self.folder = await self.ainjector(VmFolder, self.config_layout.vmware.folder)
            return await super().async_ready()

    async def _network_dict(self):
        if not self.network_config: return []
        network_config = await self.ainjector(self.network_config.resolve, access_class = network.DistributedPortgroup)
        if self.paravirt:
            net_type = "vmxnet3"
        else: net_type = "e1000"
        switch = self.config_layout.vmware.distributed_switch
        res = []
        for net, i, mac in network_config:
            d = {'device_type': net_type,
                 'name': net.full_name,
                 "dvswitch_name": switch,}
            if mac: d['mac'] = mac
            res.append(d)
        return res
    
                      
                
            
        
    async def _ansible_dict(self, **kwargs):
        config = self.config_layout.vmware
        if self.paravirt:
            scsi_type = "paravirtual"
            network_type = "vmxnet3"
        else:
            raise NotImplementedError("Loop up what we want to use in non-paravirt mode")
        d = await self.ainjector(vmware_dict,
                                     name = self.full_name,
                                     folder = self.folder.name,
                                     cluster = config.cluster,
                                 datastore = self.storage.name,)
        if not self.inventory_object:
            d.update(
                guest_id = self.guest_id,
                hardware = dict(
                    boot_firmware = config.hardware.boot_firmware,
                    num_cpus = self.cpus,
                    memory_mb = self.memory,
                    version = config.hardware.version,
                    scsi = scsi_type,
                    nested_virt = self.nested_virt),
                disk = [dict(
                    size_gb = self.disk_size,
                    type = "thin")],
                networks = await self._network_dict(),
            )
            if self.template_name:
                d['template'] = self.template_name
                if self.template_snapshot:
                    d['linked_clone'] = True
                    d['snapshot_src'] = self.template_snapshot
        d.update(kwargs)
        return d

    async def _ansible_op(self, **kwargs):
        d = await self.ainjector(self._ansible_dict, **kwargs)
        try:
            del self.__dict__['inventory_object']
        except KeyError: pass
        return await self.ainjector(carthage.ansible.run_play,
                                    [carthage.ansible.localhost_machine],
                                    {'vmware_guest': d})

    
    @setup_task("provision_vm")
    async def create_vm(self):
        res =  await self.ainjector(self._ansible_op, state='present')
        await self.ainjector(self._find_inventory_object)
        return res

    @create_vm.invalidator()
    async def create_vm(self):
        await self.ainjector(self._find_inventory_object)
        return self.inventory_object

    async def _find_inventory_object(self):
        self.inventory_object = self.folder.inventory_view.find_by_name(self.full_name)
        

    
    async def delete_vm(self):
        return await self.ainjector(self._ansible_op, state = 'absent', force = True)

    async def start_machine(self):
        loop = self.injector.get_instance(asyncio.AbstractEventLoop)
        async with self._operation_lock:
            if not self.running:
                logger.debug(f'Starting dependencies for {self.name}')
                await self.start_dependencies()
                logger.info(f'Starting {self.name} VM')
                power = self.inventory_object.summary.runtime.powerState
                if power != "poweredOn": 
                    task = self.inventory_object.PowerOn()
                    await inventory.wait_for_task(task)
                    if task.info.state != 'success':
                        raise RuntimeError(task.info.error)
                self.running = True
            if self.__class__.ip_address is Machine.ip_address:
                await loop.run_in_executor(None, self._get_ip_address)
            return True

    def _get_ip_address(self):
        for i in range(20):
            try:
                nets = self.inventory_object.guest.net
                nets.sort(key = lambda x: x.deviceConfigId)
                ip =nets[0].ipAddress[0]
                self.ip_address = ip
                return
            except IndexError: pass
            time.sleep(5)
        raise TimeoutError(f'Unable to get IP address for {self.name}')
    
                
            
    

@inject(
    config = ConfigLayout,
    injector = Injector,
    folder = InjectionKey(VmFolder, optional = True),
    network_config = InjectionKey(carthage.network.NetworkConfig, optional = True),
    storage = VmwareDataStore,
)
class VmTemplate(Vm):
    clone_from_snapshot = "template_snapshot"

    def __init__(self, disk, **kwargs):
        self.disk = disk
        if disk:
            kwargs['name'] = disk.image.name
            kwargs['template'] = None
            self.clone_from_snapshot = None #Doesn't tend to work with explicit disks
        super().__init__( **kwargs)

    async def _ansible_dict(self, **kwargs):
        d = await self.ainjector(super()._ansible_dict, **kwargs)
        if self.disk:
            d['disk'][0]['filename'] = self.disk.disk_path
        return d

    @setup_task("create_clone_snapshot")
    @inject(loop = asyncio.AbstractEventLoop)
    async def create_clone_snapshot(self, loop):
        if self.clone_from_snapshot is None:  raise SkipSetupTask
        t = self.inventory_object.CreateSnapshot("template_snapshot", "Snapshot for template clones", False, True)
        await inventory.wait_for_task(t)

    @setup_task("Mark as template")
    async def mark_as_template(self):
        try:
            self.inventory_object.MarkAsTemplate()
        except Exception: pass

from . import inventory
