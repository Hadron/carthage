# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from ..dependency_injection import *
from ..config import config_defaults, ConfigLayout

from ..network import Network, TechnologySpecificNetwork, this_network
from .inventory import VmwareStampable, VmwareFolder, VmwareConnection

config_defaults.add_config({'vmware': {
    'distributed_switch': None
    }})

@inject(config_layout = ConfigLayout,
        injector = Injector,
        connection = VmwareConnection)
class NetworkFolder(VmwareFolder):
    kind = 'network'

@inject(folder = NetworkFolder,
        network = this_network,
        config_layout = ConfigLayout,
        injector = Injector)
class VmwareNetwork(VmwareStampable, TechnologySpecificNetwork):

    # fill me min to create a port group, and stuff.

    def __init__(self, network, *, folder, config_layout, injector):
        super().__init__()
        print(f"Constructing port group for {network.name} on vlan {network.vlan_id} using switch {config_layout.vmware.distributed_switch}")
        # do stuff
