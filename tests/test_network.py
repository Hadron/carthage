# Copyright (C) 2018, 2019, 2020, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.pytest import *
import pytest
import posix
from ipaddress import *
from carthage import base_injector
import carthage.network.config
from carthage.network import Network, BridgeNetwork, V4Config
from carthage.dependency_injection import *
from carthage.modeling import *


@pytest.fixture()
def injector():
    if posix.geteuid() != 0:
        pytest.skip("Not running as root; network tests skipped", )
    return base_injector.claim()


@async_test
async def test_network_create(injector, loop):
    ainjector = injector(AsyncInjector)
    net = await ainjector(Network, name="testnet")
    net.close()


@pytest.mark.xfail(reason="Needs adjusting for namespace")
@async_test
async def test_network_veth(injector, loop):
    ainjector = injector(AsyncInjector)
    net = await ainjector(Network, name="testnet")
    net = await net.access_by(BridgeNetwork)
    ve = net.add_veth('database.hadronindustries.com')
    net.close()


@async_test
async def test_v4_config_secondary_expand(ainjector):
    '''Test v4_config with deferred elements and secondary addresses
    '''
    def address():
        return '10.1.0.1'
    class layout(CarthageLayout):
        @provides("net")
        class net(NetworkModel):
            name = 'net_1'
            v4_config = V4Config(network='10.0.0.0/8')

        class machine(MachineModel):
            class net_config(NetworkConfigModel):
                add('eth0', net=net,
                    mac=None,
                    v4_config=V4Config(
                        secondary_addresses = [address],
                        ))

    l = await ainjector(layout)
    config = l.machine.network_links['eth0'].merged_v4_config
    assert config.secondary_addresses == [carthage.network.config.SecondaryAddress(private=IPv4Address(address()))]
    assert IPv4Address(address()) in config.network

    
        
