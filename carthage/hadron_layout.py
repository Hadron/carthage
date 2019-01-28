from .network import Network, NetworkConfig, BridgeNetwork
from .hadron import hadron_container_image, TestDatabase, database_key
from .dependency_injection import InjectionKey, inject
from .utils import when_needed
from .container import Container


external_network =when_needed(Network, 'external network', 
                              vlan_id = 0, addl_keys = ['external-network'])
external_bridge = when_needed(BridgeNetwork, bridge_name = "brint", delete_bridge = False)

fake_internet = when_needed(Network, 'vpn',
                            addl_keys = ['fake-internet', 'vpn-network'])

external_bridge_key = InjectionKey(BridgeNetwork, vlan_id = 0)
database_network_config = NetworkConfig()
database_network_config.add('eth0', InjectionKey('external-network'), None)
database_network_config.add('eth1',  InjectionKey('fake-internet'), None)



test_database_container = when_needed(TestDatabase, image = hadron_container_image, network_config = database_network_config)


@inject(
    slot = InjectionKey('this_slot'))
def mac_from_database(interface, slot):
    if slot.item is None: return None
    return getattr(slot.item.machine, interface)


router_network_config = NetworkConfig()
router_network_config.add('eth0', InjectionKey('vpn-network'), mac_from_database)
router_network_config.add('eth1', InjectionKey('site-network'), mac_from_database)

site_network_config = NetworkConfig()
site_network_config.add('eth0', InjectionKey('site-network'), mac_from_database)
