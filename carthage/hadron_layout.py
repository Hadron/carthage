# Copyright (C) 2018, 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .network import Network, NetworkConfig, external_network_key, HostMapEntry, host_map_key
from .hadron.images import hadron_container_image, TestDatabase, database_key, HadronVaultContainer
from .dependency_injection import InjectionKey, inject
from .utils import when_needed
from .container import Container



fake_internet = when_needed(Network, 'vpn',
                            vlan_id = 1000,
                            addl_keys = ['fake-internet', 'vpn-network'])

services_vlan = when_needed(Network, "n103", vlan_id = 103,)

database_network_config = NetworkConfig()
database_network_config.add('eth0', external_network_key, None)
database_network_config.add('eth1',  InjectionKey('fake-internet'), None)
database_network_config.add('eth2', services_vlan, None)




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

hadron_host_map = {
    'vault.hadronindustries.com': HostMapEntry(
        mac = "00:50:56:97:3e:be",
        ip = '192.168.103.2'),
    }
