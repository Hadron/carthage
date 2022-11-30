# Copyright (C) 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *

from .utils import wait_for_task
from .folder import VmwareFolder, HostFolder
from .inventory import VmwareNamedObject
from .cluster import VmwareCluster
from pyVmomi import vim

__all__ = ['VmwareHost']


class VmwareHost(VmwareNamedObject, kind='host'):

    parent_type = (VmwareCluster, HostFolder)

    def __init__(self, name, **kwargs):
        if not name.startswith('/') and not 'parent' in kwargs:
            self.connection = kwargs['connection']  # for _find_by_name
            if not 'mob' in kwargs:
                kwargs['mob'] = self._find_by_name(name)
            kwargs['parent'] = self._parent_path_from_mob(kwargs['mob'].parent)
        kwargs['name'] = name
        self.spec = kwargs.pop('spec', None)
        super().__init__(**kwargs)

    def _find_parent(self):
        if (self.mob is not None) and isinstance(self.mob.parent, vim.ClusterComputeResource):
            self.parent_type = VmwareCluster
        else:
            self.parent_type = HostFolder
        return super()._find_parent()

    def _find_by_name(self, name):
        vm = self.connection.content.viewManager
        container = vm.CreateContainerView(self.connection.content.rootFolder, [vim.HostSystem], True)
        found = None
        for ref in container.view:
            if ref.name == name:
                found = ref
                break
        container.Destroy()
        return found

    async def do_create(self):
        task = self.parent.mob.AddHost_Task(spec=self.spec, asConnected=True)
        await wait_for_task(task)
        self.mob = task.info.result
        return self.mob
