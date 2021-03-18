from .base import *
from .decorators import *
from carthage.network import NetworkConfig
from carthage import InjectionKey

class RouterConfig(NetworkConfigModel):

    internet = injector_access("internet")
    site_network = injector_access("site-network")
    add("eth0", internet, None)
    add("eth1", site_network,  None)
    
class Layout(ModelGroup):

    class net_config(NetworkConfigModel):
        site_network = injector_access("site-network")
        add("eth0", site_network, None)
        
    @provides(InjectionKey("internet"))
    class Internet(NetworkModel):
        

#        bridge_name = "brint"
        pass
        
    class Red(Enclave):
        domain = "evil.com"

        @provides("site-network")
        class RedNet(NetworkModel):
            pass


        class router(MachineModel):
            add_provider(InjectionKey(NetworkConfig), RouterConfig)

        class samba(MachineModel):
            ansible_groups = ['samba']

        for u in ('george', 'sue', 'pat'):
            @dynamic_name(f'{u}_desktop')
            class desktop(MachineModel):
                name = f'{u}-desktop'
        del u

        
