from carthage import *

from .folder import VmwareFolder, HostFolder
from .inventory import VmwareSpecifiedObject
from .cluster import VmwareCluster
from pyVmomi import vim

__all__ = ['VmwareHost']

@inject(**VmwareSpecifiedObject.injects)
class VmwareHost(VmwareSpecifiedObject, kind='host'):
    parent_type = (VmwareCluster, HostFolder)

    def __init__(self, name, **kwargs):
        if not name.startswith('/') and not 'parent' in kwargs:
            self.connection = kwargs['connection'] # for _find_by_name
            if not 'mob' in kwargs:              kwargs['mob'] = self._find_by_name(name)
            kwargs['parent'] = self._parent_path_from_mob(kwargs['mob'].parent)
        kwargs['name'] = name
        super().__init__(**kwargs)

    def _find_parent(self):
        if isinstance(self.mob.parent, vim.ClusterComputeResource):
            self.parent_type = VmwareCluster
        else: self.parent_type = HostFolder
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

