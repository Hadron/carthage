from carthage import *

import asyncio, logging, time

import carthage.ansible, carthage.network

from .image import VmwareDataStore, VmdkTemplate
from .inventory import VmwareNamedObject
from .connection import VmwareConnection
from .folder import VmwareFolder

logger = logging.getLogger('carthage.vmware.vm')

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

@inject(**VmwareFolder.injects)
class VmFolder(VmwareFolder, kind='vm'):

    @memoproperty
    def inventory_view(self):
        return self.injector(VmInventory, folder=self)

    async def delete(self):
        task = self.mob.Destroy_Task()
        # await carthage.vmware.utils.wait_for_task(task)

    async def prevdelete(self):
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
        return await utils.wait_for_task(self.inventory_object.Destroy())
    

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
    **VmwareNamedObject.injects,
    storage = VmwareDataStore,
    folder = InjectionKey(VmFolder, optional = True),
    network_config = InjectionKey(carthage.network.NetworkConfig, optional = True),
    # We add a template VM to the dependencies later avoiding a forward reference
    )
class Vm(Machine, VmwareNamedObject):

    stamp_type = "vm"
    parent_type = VmFolder

    nested_virt = False #: Enable nested virtualization
    def __init__(self, name, template,
                 guest_id = "ubuntu64Guest",
                 *args, storage, folder, network_config = None,
                 console_needed = True, **kwargs):
        VmwareNamedObject.__init__(self, name, **kwargs)
        # super().__init__(name, injector, config)
        self.storage = storage
        self.running = False
        self.folder = folder
        vm_config = self.config_layout.vmware
        self.cpus = vm_config.hardware.cpus
        self.memory = vm_config.hardware.memory
        self.paravirt = vm_config.hardware.paravirt
        self.disk_size = vm_config.hardware.disk
        if self.config_layout.vm_image_size > self.disk_size*1000000000:
            self.disk_size = int(self.config_layout.vm_image_size/1000000000)
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
            if False:
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
                    try: del d['disk']
                    except KeyError: pass
        d.update(kwargs)
        return d

    async def _ansible_op(self, **kwargs):
        await self.ainjector(self._find_inventory_object)
        d = await self.ainjector(self._ansible_dict, **kwargs)
        return await self.ainjector(carthage.ansible.run_play,
                                    [carthage.ansible.localhost_machine],
                                    {'vmware_guest': d})

    async def do_create(self):
        try:
            res =  await self.ainjector(self._ansible_op, state='present')
        except carthage.ansible.AnsibleFailure as e:
            if "is not supported" in e.ansible_result.tasks['vmware_guest'].msg:
                #Everything but customization worked
                res = e.ansible_result
            else:
                raise
            
        await self.ainjector(self._find_inventory_object)
        return res

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

    async def stop_machine(self):
        async with self._operation_lock:
            if not self.running: return
            logger.info(f'Stopping {self.name} VM')
            power = self.inventory_object.summary.runtime.powerState
            if power == "poweredOn": 
                task = self.inventory_object.PowerOff()
                await inventory.wait_for_task(task)
            self.running = False
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

    def __init__(self, name, disk, **kwargs):
        self.disk = disk
        if disk:
            kwargs['template'] = None
            self.clone_from_snapshot = None #Doesn't tend to work with explicit disks
        super().__init__( name = name, **kwargs)
        self.network_config = None

    async def _ansible_dict(self, **kwargs):
        d = await self.ainjector(super()._ansible_dict, **kwargs)
        if self.disk and not self.inventory_object:
            if not isinstance(self.disk, VmdkTemplate):
                logger.info(f"Building disk for {self}")
                self.disk = await self.ainjector(self.disk)
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
        # ESXI 6.7 seems to have a bug  producing linked clones from templates rather than VMs
        if self.clone_from_snapshot: raise SkipSetupTask
        try:
            self.inventory_object.MarkAsTemplate()
        except Exception: pass

@inject(folder = VmwareFolder, connection = VmwareConnection)
class VmInventory(Injectable):

    def __init__(self, *, folder, connection):
        self.view = connection.content.viewManager.CreateContainerView(
            folder.inventory_object,
            [vim.VirtualMachine], True)

    def find_by_name(self, name):
        for v in self.view.view:
            if v.name == name: return v
        return None

    def close(self):
        try:
            self.view.Destroy()
            del self.view
        except: pass

#Now mark VM as taking a template
inject(template = InjectionKey(VmTemplate, optional = True))(Vm)
from . import inventory
