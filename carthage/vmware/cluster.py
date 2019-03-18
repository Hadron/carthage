# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

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
