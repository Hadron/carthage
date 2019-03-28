from pyVmomi import vim, vmodl

from carthage import *
from carthage.console import *

from .inventory import VmwareSpecifiedObject
from .folder import VmwareFolder

@inject(**VmwareFolder.injects)
class DataStoreFolder(VmwareFolder, kind='datastore'):
    pass

@inject(**VmwareSpecifiedObject.injects)
class VmwareDataStoreCluster(VmwareSpecifiedObject, kind='datastore'):

    parent_type = DataStoreFolder


    async def do_create(self):
        self.parent.mob.CreateStoragePod(name=self.name)

    async def delete(self):
        task = self.mob.Destroy_Task()
        await carthage.vmware.utils.wait_for_task(task)

@inject(**VmwareSpecifiedObject.injects)
class VmwareDataStore(VmwareSpecifiedObject, kind='datastore'):

    parent_type = (VmwareDataStoreCluster, DataStoreFolder)

    def __init__(self, name, *args, host=None, hosts=None, **kwargs):

        if (hosts is not None) and (host is not None):
            raise ValueError('specify only one of host or hosts')
        elif (hosts is None) and (host is not None):
            self.hosts = [ host ]
        elif (hosts is not None) and (host is None):
            self.hosts = None
        else:
            # None here means that we should use what we find.
            self.hosts = hosts
        if not name.startswith('/') and 'parent' not in kwargs:
            vmc = kwargs['config_layout'].vmware
            kwargs['parent'] = f'/{vmc.datacenter}/datastore'
            self.parent_type = DataStoreFolder
        kwargs['name'] = name
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

    async def delete(self):
        task = self.mob.Destroy_Task()
        await carthage.vmware.utils.wait_for_task(task)
