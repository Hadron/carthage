# Copyright (C) 2019, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import logging
import time
from dataclasses import dataclass
import carthage.ansible
import carthage.network
import os.path
from ..machine import Machine
from ..dependency_injection import *
from ..config import ConfigLayout, config_key
from ..setup_tasks import SetupTaskMixin, setup_task, SkipSetupTask
from ..utils import memoproperty
from .image import VmwareDataStore, VmdkTemplate
from .inventory import VmwareStampable
from .connection import VmwareConnection
from .folder import VmwareFolder
from . import network

logger = logging.getLogger('carthage.vmware')


class VmwarePrincipal(object):
    def __init__(self, principal):
        self.principal = principal


class VmwareUser(VmwarePrincipal):
    @property
    def group(self): return False


class VmwareGroup(VmwarePrincipal):
    @property
    def group(self): return True


class VmwareAuthorizationRole(VmwareStampable, kind='authorization_role'):

    def __init__(self, name, privIds=None, *args, **kwargs):
        self.name = name
        self.privIds = privIds
        VmwareStampable.__init__(self, *args, **kwargs)

    @memoproperty
    def stamp_descriptor(self):
        return self.name

    def _find_role(self):
        am = self.connection.content.authorizationManager
        for role in am.roleList:
            if role.name == self.name:
                self.mob = role
                return role

    @setup_task("create role")
    async def create(self):
        am = self.connection.content.authorizationManager
        role = self._find_role()
        if role is None:
            if self.privIds is None:
                raise KeyError
            am.AddAuthorizationRole(name=self.name, privIds=self.privIds)
        else:
            if self.privIds is not None:
                am.UpdateAuthorizationRole(roleId=role.roleId, newName=self.name, privIds=self.privIds)

    @create.invalidator()
    async def create(self, **kwargs):

        def lists_equal(a, b):
            return list_is_subset(a, b) and list_is_subset(b, a)

        def list_is_subset(a, b):
            for x in a:
                if x not in b:
                    return False
            return True

        role = self._find_role()
        if role is not None and self.privIds is not None:
            if not lists_equal(role.privilege, self.privIds):
                return None
        return role


@dataclass
class VmwarePermission():
    principal: VmwarePrincipal
    role: VmwareAuthorizationRole
    propagate: bool


@inject(ainjector=AsyncInjector, config=ConfigLayout)
async def create_roles(*, ainjector, config):

    cr = create_roles

    await ainjector(VmwareAuthorizationRole,
                    name='Carthage Content Creation',
                    privIds=cr.view + cr.create + cr.vm + cr.datastore + cr.globals)

    await ainjector(VmwareAuthorizationRole,
                    name='Carthage Visibility',
                    privIds=cr.view + cr.globals)

    await ainjector(VmwareAuthorizationRole,
                    name='Carthage Maintenance',
                    privIds=cr.view + cr.maintain + cr.vm + cr.datastore + cr.globals)

    await ainjector(VmwareAuthorizationRole,
                    name='Carthage Content Owner',
                    privIds=cr.view + cr.create + cr.manage +
                    cr.maintain + cr.delete + cr.vm +
                    cr.packer + cr.datastore + cr.globals)

create_roles.globals = [
    'Global.SetCustomField'
]
create_roles.view = [
    'System.View',
    'System.Anonymous',
    'System.Read'
]
create_roles.create = [
    'Datastore.Move',
    'Host.Config.Storage',
    'Host.Inventory.CreateCluster',
    'Host.Inventory.EditCluster',
    'Folder.Create',
    'DVPortgroup.Create'
]
create_roles.manage = [
    'Global.SetCustomField',
    'Authorization.ModifyPermissions',
    'VirtualMachine.Inventory.Move',
    'Host.Inventory.MoveHost',
    'Host.Inventory.EditCluster',
    'Host.Config.Maintenance'
]
create_roles.maintain = [
    'Resource.HotMigrate',
    'Resource.ColdMigrate'
]
create_roles.delete = [
    'Host.Inventory.DeleteCluster',
    'Folder.Delete',
    'DVPortgroup.Delete'
]
# Based on https://github.com/jetbrains-infra/packer-builder-vsphere/issues/97
create_roles.packer = [
    # Create VM:
    'VirtualMachine.Inventory.Create',
    'VirtualMachine.Inventory.Delete',
    'VirtualMachine.Config.AddNewDisk',
    # Customize hardware:
    'VirtualMachine.Config.CPUCount',
    'VirtualMachine.Config.Resource',
    'VirtualMachine.Config.Memory',
    'VirtualMachine.Config.Settings',
    'VirtualMachine.Config.Annotation',
    # configuration_parameters
    'VirtualMachine.Config.AdvancedConfig',
    # Boot
    'VirtualMachine.Config.Settings',
    'VirtualMachine.Interact.PowerOn',
    'VirtualMachine.Interact.ConsoleInteract',
    'VirtualMachine.Interact.PowerOff',
    # CD-ROM
    'VirtualMachine.Config.AddRemoveDevice',
    'VirtualMachine.Interact.SetCDMedia',
    'VirtualMachine.Interact.DeviceConnection',
    # Upload floppy image
    'VirtualMachine.Config.AddRemoveDevice',
    'VirtualMachine.Interact.SetFloppyMedia',
    # Snapshot:
    'VirtualMachine.State.CreateSnapshot',
    # Template:
    'VirtualMachine.Provisioning.MarkAsTemplate'
]
create_roles.vm = [
    'VirtualMachine.Config.Rename',
    'VirtualMachine.Config.Rename',
    'VirtualMachine.Config.Annotation',
    'VirtualMachine.Config.Annotation',
    'VirtualMachine.Config.AddNewDisk',
    'VirtualMachine.Config.RemoveDisk',
    'VirtualMachine.Config.RawDevice',
    'VirtualMachine.Config.HostUSBDevice',
    'VirtualMachine.Config.CPUCount',
    'VirtualMachine.Config.Memory',
    'VirtualMachine.Config.AddRemoveDevice',
    'VirtualMachine.Config.EditDevice',
    'VirtualMachine.Config.Settings',
    'VirtualMachine.Config.Resource',
    'VirtualMachine.Config.UpgradeVirtualHardware',
    'VirtualMachine.Config.ResetGuestInfo',
    'VirtualMachine.Config.ToggleForkParent',
    'VirtualMachine.Config.AdvancedConfig',
    'VirtualMachine.Config.DiskLease',
    'VirtualMachine.Config.SwapPlacement',
    'VirtualMachine.Config.DiskExtend',
    'VirtualMachine.Config.ChangeTracking',
    'VirtualMachine.Config.QueryUnownedFiles',
    'VirtualMachine.Config.ReloadFromPath',
    'VirtualMachine.Config.QueryFTCompatibility',
    'VirtualMachine.Config.MksControl',
    'VirtualMachine.Config.ManagedBy',
    'VirtualMachine.Inventory.Create',
    'VirtualMachine.Inventory.Delete',
    'VirtualMachine.Interact.PutUsbScanCodes',
    'VirtualMachine.Config.AddExistingDisk',
    'VirtualMachine.Config.AddRemoveDevice',
    'VirtualMachine.Config.AddNewDisk',
    'VirtualMachine.Interact.PowerOn',
    'VirtualMachine.Interact.PowerOff',
    'VirtualMachine.Inventory.CreateFromExisting',
    'VirtualMachine.Provisioning.CreateTemplateFromVM',
    'VirtualMachine.Provisioning.DeployTemplate',
    'VirtualMachine.Provisioning.CloneTemplate',
    'VirtualMachine.Provisioning.Clone',
    'VirtualMachine.Provisioning.MarkAsVM',
    'Resource.AssignVMToPool',
    'Network.Assign',
    'VirtualMachine.State.CreateSnapshot',
    'VirtualMachine.State.RevertToSnapshot',
    'VirtualMachine.Interact.AnswerQuestion',
    'VirtualMachine.Interact.ConsoleInteract',
    'VirtualMachine.Interact.DeviceConnection',
    'VirtualMachine.Interact.Reset',
    'VirtualMachine.Interact.SetCDMedia',
    'VirtualMachine.Interact.SetFloppyMedia',
    'VirtualMachine.Interact.ToolsInstall',
    'VApp.Import'
]
create_roles.datastore = [
    'Datastore.AllocateSpace',
    'Datastore.Browse',
    'Datastore.DeleteFile',
    'Datastore.FileManagement',
    'Datastore.UpdateVirtualMachineFiles'
]
create_roles.datastore_view = [
    'Datastore.Browse'
]
