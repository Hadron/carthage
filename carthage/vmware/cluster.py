from carthage import *

from .inventory import VmwareSpecifiedObject
from .folder import HostFolder

@inject(**VmwareSpecifiedObject.injects)
class VmwareCluster(VmwareSpecifiedObject, kind='cluster'):

    parent_type = HostFolder

    async def do_create(self):
        self.mob = self.parent.mob.CreateClusterEx(self.name, self.spec)
        return self.mob
