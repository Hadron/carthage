# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *

from .inventory import VmwareNamedObject


class VmwareDatacenter(VmwareNamedObject, kind='datacenter'):

    def __init__(self, *args, config_layout, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = config_layout.vmware.datacenter
            kwargs['readonly'] = kwargs.get('readonly', True)
        super().__init__(*args, **kwargs, config_layout=config_layout)

    async def async_ready(self):
        from .datastore import DataStoreFolder
        from .host import HostFolder
        from .network import NetworkFolder
        from .vm import VmFolder
        self.datastore_folder = await self.ainjector(DataStoreFolder, name='datastore', parent=self)
        self.host_folder = await self.ainjector(HostFolder, name='host', parent=self)
        self.network_folder = await self.ainjector(NetworkFolder, name='network', parent=self)
        self.vm_folder = await self.ainjector(VmFolder, name='vm', parent=self)
        ret = await super().async_ready()
        return ret

    async def do_create(self):
        content = self.connection.content
        content.rootFolder.CreateDatacenter(name=self.name)

    parent_type = type(None)

    is_root = True
