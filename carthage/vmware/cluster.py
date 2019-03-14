from carthage import *

from .inventory import VmwareSpecifiedObject

@inject(**VmwareSpecifiedObject.injects)
class VmwareCluster(VmwareSpecifiedObject, kind='cluster'):

    def vmware_path_for(self, child):
        from .host import VmwareHost
        if isinstance(child, VmwareHost):
            return super().vmware_path_for(self)
        return super().vmware_path_for(child)

    async def do_create(self):
        assert self.writable
        self.inventory_object = self.parent.mob.CreateClusterEx(self.name, self.spec)
        return self.inventory_object
