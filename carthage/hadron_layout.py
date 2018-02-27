# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .network import Network, NetworkConfig
from .hadron import hadron_container_image, TestDatabase, database_key
from .dependency_injection import InjectionKey, inject
from .utils import when_needed
from .container import Container


external_network =when_needed(Network, 'brint', delete_bridge = False,
                              addl_keys = ['external-network'])
fake_internet = when_needed(Network, 'vpn',
                            addl_keys = ['fake-internet', 'vpn-network'])

database_network_config = NetworkConfig()
database_network_config.add('eth0', InjectionKey('external-network'), None)
database_network_config.add('eth1',  InjectionKey('fake-internet'), None)



test_database_container = when_needed(TestDatabase, image = hadron_container_image, network_config = database_network_config)


@inject(
    slot = InjectionKey('this_slot'))
def mac_from_database(interface, slot):
    return getattr(slot.item.machine, interface)


router_network_config = NetworkConfig()
router_network_config.add('eth0', InjectionKey('vpn-network'), mac_from_database)
router_network_config.add('eth1', InjectionKey('site-network'), mac_from_database)
