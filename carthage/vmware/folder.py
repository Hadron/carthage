# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *

from .inventory import *

__all__ = ['VmwareFolder', 'HostFolder']


class VmwareFolder(VmwareNamedObject):

    '''
If a fully qualified name (in the form that ``searchIndex.FindByInventoryPath`` would recognize is passed in, then that is used directly as the :meth:`vmware_path`.  If a path is passed in without a leading slash, then we look up ``vmware.datacenter`` in the :class:`.ConfigLayout`  and start from the appropriate kind of folder for that data center.
    '''

    def __init_subclass__(cls, kind=NotImplemented, **kwargs):
        kwargs['kind'] = kind
        from . import datacenter
        if kind is not NotImplemented:
            cls.parent_type = (cls, datacenter.VmwareDatacenter)
        super().__init_subclass__(**kwargs)

    def __init__(self, name, *args, **kwargs):
        from . import datacenter
        if name.startswith('/') or 'parent' in kwargs:
            kwargs['name'] = name
        else:  # No initial slash, no explicit parent
            dc_name = kwargs['config_layout'].vmware.datacenter
            name = f'/{dc_name}/{self.stamp_type}/{name}'
            kwargs['name'] = name
        super().__init__(*args, **kwargs)
        if self.parent:
            self.parent_type = type(self.parent)
        else:
            if '/' in self.parent_path[1:]:
                self.parent_type = type(self)
            else:
                self.parent_type = datacenter.VmwareDatacenter

    async def do_create(self):
        if not self.parent:
            raise ValueError(f"unable to create folder '{self.name}' as no parent was specified")
        return self.parent.mob.CreateFolder(self.name)

    async def delete(self):
        task = self.mob.Destroy_Task()
        await carthage.vmware.utils.wait_for_task(task)


class HostFolder(VmwareFolder, kind='host'):
    pass
