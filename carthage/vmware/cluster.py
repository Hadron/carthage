from carthage import *

from .inventory import VmwareSpecifiedObject
from .folder import HostFolder

@inject(**VmwareSpecifiedObject.injects)
class VmwareCluster(VmwareSpecifiedObject, kind='cluster'):

    parent_type = HostFolder

    def __init__(self, name = None, parent = None, mob = None, **kwargs):
        if not mob and not parent and not name:
            vmc = kwargs['config_layout'].vmware
            parent = f'/{vmc.datacenter}/host'
            name = vmc.cluster
        super().__init__(name = name, parent = parent, mob = mob, **kwargs)
        
    async def do_create(self):
        self.mob = self.parent.mob.CreateClusterEx(self.name, self.spec)
        return self.mob
