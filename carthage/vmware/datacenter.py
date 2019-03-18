from carthage import *

from .inventory import VmwareNamedObject

@inject(**VmwareNamedObject.injects)
class VmwareDatacenter(VmwareNamedObject, kind='datacenter'):

    # typemap = {
    #     Vm : 'vm',
    #     VmFolder : 'vm',
    #     VmwareNetwork : 'network',
    #     NetworkFolder : 'network',
    #     DvSwitch : 'network',
    #     DistributedPortgroup : 'network',
    #     VmwareCluster : 'host',
    #     HostFolder : 'host',
    #     DatastoreFolder : 'datastore',
    #     VmwareDatastore :  'datastore',
    #     VmwareDatastoreCluster :  'datastore',
    # }

    # foldermap = { v : k for k, v in typemap.items() if issubclass(k, VmwareFolder) }
    # foldertypes = tuple(foldermap.values())

    def __init__(self, *args, config_layout, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = config_layout.vmware.datacenter
            kwargs['readonly'] = kwargs.get('readonly', True)
        super().__init__(*args, **kwargs, config_layout=config_layout)

    parent_type = type(None)

    is_root = True
