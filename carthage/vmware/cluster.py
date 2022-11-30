# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *

from .inventory import VmwareNamedObject
from .folder import HostFolder


class VmwareCluster(VmwareNamedObject, kind='cluster'):

    parent_type = HostFolder

    def __init__(self, name=None, parent=None, mob=None, **kwargs):
        if not mob and not parent and not name:
            vmc = kwargs['config_layout'].vmware
            parent = f'/{vmc.datacenter}/host'
            name = vmc.cluster
        self.spec = kwargs.pop('spec', None)
        super().__init__(name=name, parent=parent, mob=mob, **kwargs)

    async def do_create(self):
        self.mob = self.parent.mob.CreateClusterEx(self.name, self.spec)
        return self.mob
