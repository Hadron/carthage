# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *

import asyncio
import logging
import time
from pathlib import Path
import carthage.network

from .image import VmwareDataStore, VmdkTemplate, vm_datastore_key
from .inventory import VmwareNamedObject, VmwareSpecifiedObject
from .connection import VmwareConnection
from .folder import VmwareFolder
from .cluster import VmwareCluster
from .datacenter import VmwareDatacenter
from .utils import wait_for_task
from . import network
from .config_spec import *

from pyVmomi import vim, vmodl

logger = logging.getLogger('carthage.vmware.vm')


class VmwareVmConfig(ConfigSchema, prefix="vmware"):
    datacenter: str
    folder: str = "carthage"
    cluster: str


class HardwareConfig(ConfigSchema, prefix="vmware.hardware"):
    boot_firmware: str = "efi"
    version: int = 14
    memory_mb: int = 4096
    disk: int = 25000000000
    cpus: int = 1
    paravirt: bool = True


vmware_config = config_key('vmware')


class VmFolder(VmwareFolder, kind='vm'):

    def __init__(self, name=None, **kwargs):
        if name is None:
            name = kwargs['config_layout'].vmware.folder
        kwargs['name'] = name
        kwargs.setdefault('readonly', False)
        super().__init__(**kwargs)

    @memoproperty
    def inventory_view(self):
        return self.injector(VmInventory, folder=self)

    async def delete(self, cluster=None):
        v = None
        if self.mob is None:
            return
        try:
            v = self.children([vim.VirtualMachine])
            cluster_name = self.config_layout.vmware.cluster
            if not cluster:
                datacenter = await self.ainjector(VmwareDatacenter)
                cluster = await self.ainjector(
                    VmwareCluster, name=cluster_name,
                    parent=datacenter.host_folder if not cluster_name.startswith('/') else None)

                rp = cluster.mob.resourcePool
            for vm in v:
                try:
                    vm.MarkAsVirtualMachine(rp)
                except BaseException:
                    pass
                try:
                    vm.PowerOff()
                except BaseException:
                    pass
        finally:
            if v:
                v.close()
        await asyncio.sleep(2)
        return await wait_for_task(self.mob.Destroy())


@inject(
    storage=vm_datastore_key,
    cluster=VmwareCluster,
    parent=InjectionKey(VmFolder, optional=True),
    network_config=InjectionKey(carthage.network.NetworkConfig, optional=True),
    # We add a template VM to the dependencies later avoiding a forward reference
)
class Vm(VmwareSpecifiedObject, Machine):

    stamp_type = "vm"
    parent_type = VmFolder
    config_spec_class = vim.vm.ConfigSpec

    nested_virt = False  # : Enable nested virtualization

    def __init__(self, name, template,
                 guest_id="ubuntu64Guest",
                 *, storage, cluster, dspath=None, network_config=None,
                 console_needed=True, **kwargs):
        kwargs.setdefault('readonly', False)
        super().__init__(name=name, **kwargs)

        self.cluster = cluster
        self.storage = storage
        if dspath is None:
            self.dspath = self.config_layout.vmware.datastore.path
        else:
            self.dspath = dspath
        if self.dspath and not self.dspath.endswith('/'):
            self.dspath += '/'
        self.running = False
        self.folder = self.parent
        vm_config = self.config_layout.vmware
        self.cpus = vm_config.hardware.cpus
        self.memory = vm_config.hardware.memory_mb
        self.paravirt = vm_config.hardware.paravirt
        self.disk_size = vm_config.hardware.disk
        if self.config_layout.vm_image_size > self.disk_size:
            self.disk_size = int(self.config_layout.vm_image_size)
        self.guest_id = guest_id
        self.network_config = network_config
        self.template = None
        self.template_snapshot = None
        self.inventory_object = None
        if template:
            self.template = template
            self.template_snapshot = getattr(template, 'clone_from_snapshot', None)
        self._operation_lock = asyncio.Lock()

    def __repr__(self):
        return f"<{self.__class__.__name__} name={self.name}>"

    @classmethod
    def satisfies_injection_key(cls, k):
        if issubclass(k.target, Vm):
            if set(k.constraints) - {'role'} == set():
                return True
        return super().satisfies_injection_key(k)

    async def delete(self):
        try:
            task = self.mob.PowerOffVM_Task()
            await carthage.vmware.utils.wait_for_task(task)
        except (vim.fault.InvalidPowerState, vmodl.fault.NotSupported):
            pass
        task = self.mob.Destroy_Task()
        await carthage.vmware.utils.wait_for_task(task)
        self.mob = None
        self.vmware_uuid = None

    async def build_config(self, mode, oconfig=None, stagefilter=None):
        config = await super().build_config(mode, oconfig=oconfig, stagefilter=stagefilter)
        cur = -1
        for c in config.deviceChange:
            if c.device.key == 0:
                assert cur > -100
                c.device.key = cur
                cur = cur - 1
        return config

    async def do_create(self):
        try:
            if self.template:
                await self.template.async_become_ready()
                spec = vim.vm.CloneSpec()
                locspec = vim.vm.RelocateSpec()
                spec.location = locspec
                spec.config = await self.build_config('clone', oconfig=self.template.mob.config)
                locspec.datastore = self.storage.mob
                locspec.pool = self.cluster.mob.resourcePool
                if self.template_snapshot:
                    for s in self.template.all_snapshots():
                        if s.name == self.template_snapshot:
                            spec.snapshot = s.snapshot
                            locspec.diskMoveType = "createNewChildDiskBacking"
                            break

                res = self.template.mob.Clone(
                    self.parent.mob,
                    self.full_name,
                    spec,
                )
            else:
                config = await self.build_config('create')
                res = self.parent.mob.CreateVm(config, self.cluster.mob.resourcePool)
            await carthage.vmware.utils.wait_for_task(res)

        except carthage.ansible.AnsibleFailure as e:
            if "is not supported" in e.ansible_result.tasks['vmware_guest'].msg:
                # Everything but customization worked
                res = e.ansible_result
            else:
                raise

            self.mob = self._find_from_path()
        return res

    async def start_machine(self):
        loop = self.injector.get_instance(asyncio.AbstractEventLoop)
        async with self._operation_lock:
            if not self.running:
                logger.debug(f'Starting dependencies for {self.name}')
                await self.start_dependencies()
                logger.info(f'Starting {self.name} VM')
                power = self.mob.summary.runtime.powerState
                if power != "poweredOn":
                    task = self.mob.PowerOn()
                    await wait_for_task(task)
                    if task.info.state != 'success':
                        raise RuntimeError(task.info.error)
                self.running = True
            if self.__class__.ip_address is Machine.ip_address:
                try:
                    self.ip_address
                except NotImplementedError:
                    await loop.run_in_executor(None, self._get_ip_address)
            return True

    async def stop_machine(self):
        async with self._operation_lock:
            if not self.running:
                return
            logger.info(f'Stopping {self.name} VM')
            power = self.mob.summary.runtime.powerState
            if power == "poweredOn":
                task = self.mob.PowerOff()
                await wait_for_task(task)
            self.running = False
            return True

    def _get_ip_address(self):
        for i in range(20):
            try:
                nets = self.mob.guest.net
                nets.sort(key=lambda x: x.deviceConfigId)
                ip = nets[0].ipAddress[0]
                self.ip_address = ip
                return
            except IndexError:
                pass
            time.sleep(5)
        raise TimeoutError(f'Unable to get IP address for {self.name}')

    def all_snapshots(self):
        sn = list()

        def recurse(s):
            sn.append(s)
            for c in s.childSnapshotList:
                recurse(c)
        for s in self.mob.snapshot.rootSnapshotList:
            recurse(s)
        return sn


@inject(template=None)
class VmTemplate(Vm):

    clone_from_snapshot = "template_snapshot"

    def __init__(self, name, disk="unspecified", **kwargs):
        if disk == "unspecified" and not kwargs.get('template', None):
            raise TypeError(
                "If you want a VM Template with no disk and no parent explicitly request that by setting disk to None")
        elif disk == "unspecified":
            disk = None
        self.disk = disk
        if disk:
            kwargs['template'] = None
            self.clone_from_snapshot = None  # Doesn't tend to work with explicit disks
        super().__init__(name=name, **kwargs)
        self.network_config = None

    @setup_task("create_clone_snapshot")
    @inject(loop=asyncio.AbstractEventLoop)
    async def create_clone_snapshot(self, loop):
        if self.clone_from_snapshot is None:
            raise SkipSetupTask
        t = self.mob.CreateSnapshot("template_snapshot", "Snapshot for template clones", False, True)
        await wait_for_task(t)

    @setup_task("Mark as template")
    async def mark_as_template(self):
        # ESXI 6.7 seems to have a bug  producing linked clones from templates rather than VMs
        if self.clone_from_snapshot:
            raise SkipSetupTask
        try:
            self.mob.MarkAsTemplate()
        except Exception:
            pass


class BasicConfig(ConfigSpecStage, stage_for=Vm, order=20,
                  mode=('create', 'reconfig', 'clone')):

    def apply_config(self, config):
        obj = self.obj
        vmc = self.obj.config_layout.vmware
        if not obj.mob:
            dsname = obj.storage.name
            config.files = vim.vm.FileInfo(vmPathName=f'[{dsname}]{obj.dspath}')
        if self.bag.mode == 'create':
            config.name = obj.full_name
        config.numCPUs = obj.cpus
        config.memoryMB = obj.memory
        config.nestedHVEnabled = obj.nested_virt
        config.firmware = vmc.hardware.boot_firmware
        config.version = "vmx-" + str(vmc.hardware.version)
        config.guestId = obj.guest_id


class ScsiSpec(DeviceSpecStage, stage_for=Vm,
               order=30,
               dev_classes=(vim.vm.device.ParaVirtualSCSIController,
                            vim.vm.device.VirtualLsiLogicSASController,
                            vim.vm.device.VirtualLsiLogicController,
                            vim.vm.device.VirtualBusLogicController)):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.obj.paravirt:
            self.expected_dev = self.dev_classes[0]
        else:
            self.expected_dev = self.dev_classes[1]

    def filter_device(self, d):
        if isinstance(d, self.expected_dev):
            self.bag.scsi_key = d.key
            return True
        return False

    def new_devices(self, config):
        if getattr(self.bag, 'scsi_key', None) is None:
            d = self.expected_dev(busNumber=0,
                                  sharedBus="noSharing",
                                  key=-100)
            self.bag.scsi_key = -100
            return [d]
        return []


class DiskSpec(DeviceSpecStage,
               stage_for=Vm,
               order=120,
               dev_classes=vim.vm.device.VirtualDisk):

    disk_found = False  # Set in instances once a disk is found

    def filter_device(self, d):
        changed = False
        self.disk_found = True

        if self.bag.mode == 'clone' and d.backing.thinProvisioned is False:
            d.backing.thinProvisioned = True
            changed = True

        if False:
            if self.bag.mode == "clone":
                return d if changed else True

        if d.capacityInBytes < self.obj.disk_size:
            if (self.bag.mode == "reconfig" and d.backing.parent) or (
                    self.bag.mode == "clone" and self.obj.template_snapshot):
                raise ValueError("You cannot increase the capacity of a disk with a parent backing")
            d.capacityInBytes = self.obj.disk_size
            d.capacityInKB = int(d.capacityInBytes / 1024)
            changed = True

        if d.controllerKey != self.bag.scsi_key:
            d.controllerKey = self.bag.scsi_key
            changed = True
        return d if changed else True

    async def new_devices(self, config):
        if self.disk_found:
            return []
        d = self.dev_classes[0]()
        d.backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
        d.controllerKey = self.bag.scsi_key
        d.backing.thinProvisioned = True
        d.backing.diskMode = 'persistent'
        orig_disk = getattr(self.obj, 'disk', None)
        if orig_disk:
            if not isinstance(orig_disk, VmdkTemplate):
                orig_disk = await self.obj.ainjector(orig_disk)
            await orig_disk.async_become_ready()
            d.backing.fileName = orig_disk.disk_path
        d.capacityInBytes = self.obj.disk_size
        d.capacityInKB = int(d.capacityInBytes / 1024)
        d.unitNumber = 0
        if not orig_disk:
            self.file_operation = 'create'
        return [d]


class NetSpecStage(DeviceSpecStage,
                   stage_for=Vm,
                   order=110,
                   dev_classes=(vim.vm.device.VirtualE1000, vim.vm.device.VirtualVmxnet3),
                   ):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.obj.paravirt:
            self.expected_dev = vim.vm.device.VirtualVmxnet3
        else:
            self.expected_dev = vim.vm.device.VirtualE1000

        self.net_index = 0

    async def apply_config(self, config):
        await self.obj.resolve_networking()
        if not self.obj.network_links:
            return []
        self.network_links = list(self.obj.network_links.values())
        for n in self.network_links:
            await n.instantiate(network.DistributedPortgroup)
        await self.obj.ainjector(super().apply_config, config)

    async def new_devices(self, config):
        devs = []
        links = self.network_links[self.net_index:]
        for link in links:
            net = link.net_instance
            mac = link.mac
            if not net:
                continue
            pc = vim.dvs.PortConnection()
            pc.portgroupKey = net.mob.key
            pc.switchUuid = net.mob.config.distributedVirtualSwitch.uuid

            ds = self.expected_dev()
            ds.backing = vim.vm.device.VirtualEthernetCard.DistributedVirtualPortBackingInfo()
            ds.backing.port = pc
            if mac:
                ds.addressType = 'manual'
                ds.macAddress = mac
            devs.append(ds)
        return devs

    def filter_device(self, d):
        if self.bag.mode == 'clone':
            return False
        link = self.network_links[self.net_index]
        net = link.net_instance
        mac = link.mac
        if d.backing.port.portgroupKey != net.mob.key:
            return False
        self.net_index += 1
        return True


@inject(folder=VmwareFolder, connection=VmwareConnection)
class VmInventory(Injectable):

    def __init__(self, *, folder, connection):
        self.view = connection.content.viewManager.CreateContainerView(
            folder.mob,
            [vim.VirtualMachine], True)

    def find_by_name(self, name):
        for v in self.view.view:
            if v.name == name:
                return v
        return None

    def close(self):
        try:
            self.view.Destroy()
            del self.view
        except BaseException:
            pass


# Now mark VM as taking a template A vm can be a template for a vm, but
# we want to look up with a role for finding in the injector
inject(template=InjectionKey(Vm, role='template', _optional=True, _ready=False))(Vm)

from carthage.vm import vm_image


@inject(image=vm_image)
class vm_template_from_image(AsyncInjectable):

    @classmethod
    def default_class_injection_key(cls):
        return InjectionKey(Vm, role='template')

    async def async_resolve(self):
        vmdk = await self.ainjector(VmdkTemplate, image=self.image)
        return await self.ainjector(VmTemplate, name=(Path(self.image.path).stem) + ".template", disk=vmdk)


from . import inventory
