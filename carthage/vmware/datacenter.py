from carthage import *

from .inventory import VmwareNamedObject

@inject(**VmwareNamedObject.injects)
class VmwareDatacenter(VmwareNamedObject, kind='datacenter'):

    def __init__(self, *args, config_layout, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = config_layout.vmware.datacenter
            kwargs['readonly'] = kwargs.get('readonly', True)
        super().__init__(*args, **kwargs, config_layout=config_layout)

    async def async_ready(self):
        from .datastore import DatastoreFolder
        from .host import HostFolder
        from .network import NetworkFolder
        from .vm import VmFolder
        ret = await super().async_ready()
        self.datastore_folder = await self.ainjector(DatastoreFolder, name='datastore', parent=self)
        self.host_folder = await self.ainjector(HostFolder, name='host', parent=self)
        self.network_folder = await self.ainjector(NetworkFolder, name='network', parent=self)
        self.vm_folder = await self.ainjector(VmFolder, name='vm', parent=self)
        return ret

    parent_type = type(None)

    is_root = True
