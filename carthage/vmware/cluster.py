from carthage import *

from .inventory import VmwareSpecifiedObject
from .folder import HostFolder

@inject(**VmwareSpecifiedObject.injects)
class VmwareCluster(VmwareSpecifiedObject, kind='cluster'):

    parent_type = HostFolder

    async def do_create(self):
        assert self.writable
        self.inventory_object = self.parent.mob.CreateClusterEx(self.name, self.spec)
        return self.inventory_object
