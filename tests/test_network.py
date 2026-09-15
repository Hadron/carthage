# Copyright (C) 2018, 2019, 2020, 2023, 2025, Hadron Industries, Inc.
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
from carthage.network import Network, BridgeNetwork, V4Config, address_within_network
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

    
        
@async_test
async def test_gre_networking(ainjector):
    class layout(CarthageLayout):

        @provides("net_1")
        class net_1(NetworkModel):
            v4_config = V4Config(
                network='10.0.0.0/8',
                gateway='10.0.0.1')

        @provides('tunnel_net')
        class tunnel_net(NetworkModel):
            v4_config = V4Config(
                dhcp=False,
                network='172.31.0.0/29')

        class machine(MachineModel):
            class net_config(NetworkConfigModel):
                add('gre0', net=tunnel_net,
                    mac=None,
                    local_type='gre',
                    local='192.168.0.1',
                    remote='192.168.0.2',
                    key="34",
                    v4_config=V4Config(
                        address='172.31.0.1'),
                    destinations=[injector_access(net_1)])
    ainjector.add_provider(layout)
    l = await ainjector.get_instance_async(layout)
    link = l.machine.network_links['gre0']
    assert l.net_1 in link.destinations
    assert link.routes is None
    # The link's own network gets a [Route] block, as does each tunnel
    # destination (through the destination's gateway).
    rendering = _render_network(link)
    assert rendering.count('[Route]') == 2
    assert 'Destination=172.31.0.0/29' in rendering
    assert 'Destination=10.0.0.0/8' in rendering
    assert 'Gateway=10.0.0.1' in rendering


def _render_network(link):
    '''Render the systemd network file for *link*.'''
    import logging
    from carthage.systemd import templates_for_link, NotNeeded
    from carthage.utils import mako_lookup
    template = mako_lookup.get_template(templates_for_link(link)['network'])
    return template.render(link=link, logger=logging, NotNeeded=NotNeeded)


@async_test
async def test_link_routes(ainjector):
    '''Static routes: (Network, gateway) and (IPv4Network, IPv4Address)
    pairs render [Route] blocks with the network's v4_config.network as
    the destination.'''
    class layout(CarthageLayout):

        @provides("net")
        class net(NetworkModel):
            v4_config = V4Config(network='10.0.0.0/8')

        class machine(MachineModel):
            class net_config(NetworkConfigModel):
                add('eth0', net=net,
                    mac=None,
                    v4_config=V4Config(address='10.0.0.5'),
                    routes=[
                        (injector_access(net), '10.0.0.1'),
                        (IPv4Network('192.168.0.0/16'),
                         IPv4Address('10.0.0.2')),
                    ])

    l = await ainjector(layout)
    link = l.machine.network_links['eth0']
    # resolve_deferred turns tuples into lists
    assert [l.net, '10.0.0.1'] in link.routes
    assert [IPv4Network('192.168.0.0/16'), IPv4Address('10.0.0.2')] in link.routes

    rendering = _render_network(link)
    assert rendering.count('[Route]') == 2
    assert 'Destination=10.0.0.0/8' in rendering
    assert 'Gateway=10.0.0.1' in rendering
    assert 'Destination=192.168.0.0/16' in rendering
    assert 'Gateway=10.0.0.2' in rendering


@async_test
async def test_link_no_routes_omits_route_block(ainjector):
    '''A link with no routes and no gateway renders no [Route] block.'''
    class layout(CarthageLayout):

        @provides("net")
        class net(NetworkModel):
            v4_config = V4Config(network='10.0.0.0/8')

        class machine(MachineModel):
            class net_config(NetworkConfigModel):
                add('eth0', net=net,
                    mac=None,
                    v4_config=V4Config(address='10.0.0.5'))

    l = await ainjector(layout)
    link = l.machine.network_links['eth0']
    assert link.routes is None
    assert '[Route]' not in _render_network(link)
    
@async_test
async def test_address_within(ainjector):
    class layout(CarthageLayout):

        class                     net(NetworkModel):
            v4_config = V4Config(network='10.0.0.0/8')

        class machine(MachineModel):
            class net_config(NetworkConfigModel):
                add('eth0', mac=None, net=InjectionKey('net'),
                    v4_config=V4Config(address=address_within_network(1)))

    l = await ainjector(layout)
    assert str(l.machine.network_links['eth0'].merged_v4_config.address) == '10.0.0.1'
    
