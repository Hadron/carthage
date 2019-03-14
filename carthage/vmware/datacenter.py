# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

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

    parent_type = None

    is_root = True
