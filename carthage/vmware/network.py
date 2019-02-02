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
