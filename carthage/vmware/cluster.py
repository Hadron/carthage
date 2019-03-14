# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

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
