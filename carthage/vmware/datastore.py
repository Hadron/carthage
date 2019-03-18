# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from pyVmomi import vim, vmodl

from carthage import *
from carthage.console import *

from .inventory import VmwareSpecifiedObject
from .folder import VmwareFolder

@inject(**VmwareFolder.injects)
class DatastoreFolder(VmwareFolder, kind='datastore'):

    def vmware_path_for(self, child):
        if isinstance(child, (VmwareDatastore, VmwareDatastoreCluster)):
            return super().vmware_path_for(self)
        return super().vmware_path_for(child)

@inject(**VmwareSpecifiedObject.injects)
class VmwareDatastoreCluster(VmwareSpecifiedObject, kind='datastore'):

    parent_type = DatastoreFolder

    def vmware_path_for(self, child):
        if isinstance(child, (VmwareDatastore,)):
            return super().vmware_path_for(self)
        return super().vmware_path_for(child)

    async def do_create(self):
        self.parent.mob.CreateStoragePod(name=self.name)

    async def destroy(self):
        task = self.mob.Destroy_Task()
        await carthage.vmware.utils.wait_for_task(task)

@inject(**VmwareSpecifiedObject.injects)
class VmwareDatastore(VmwareSpecifiedObject, kind='datastore'):

    parent_type = (VmwareDatastoreCluster, DatastoreFolder)

    def __init__(self, *args, host=None, hosts=None, **kwargs):

        if (hosts is not None) and (host is not None):
            raise ValueError('specify only one of host or hosts')
        elif (hosts is None) and (host is not None):
            self.hosts = [ host ]
        elif (hosts is not None) and (host is None):
            self.hosts = None
        else:
            # None here means that we should use what we find.
            self.hosts = hosts
        super().__init__(*args, **kwargs)

    async def do_create(self):

        console = CarthageConsole(extra_locals=dict(self=self))
        console.interact()

        if self.hosts is None:
            raise ValueError(f'must specify host(s) when creating datastore {self.name}')
        if len(self.hosts) != 1:
            raise NotImplementedError(f'support for multiple hosts is not yet implemented when creating datastore {self.name}')

        ds = self.hosts[0].mob.configManager.datastoreSystem.CreateNasDatastore(spec=self.spec)
        return
        try:
            task = self.parent.mob.MoveIntoFolder_Task([ds])
            await carthage.vmware.utils.wait_for_task(task)
        except:
            ds.DestroyDatastore()
            raise

    async def destroy(self):
        task = self.mob.Destroy_Task()
        await carthage.vmware.utils.wait_for_task(task)
