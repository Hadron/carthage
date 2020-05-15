# Copyright (C) 2018, 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.pytest import *
import pytest, posix
from carthage import base_injector
from carthage.network import Network, BridgeNetwork
from carthage.dependency_injection import *

@pytest.fixture()
def injector():
    if posix.geteuid() != 0:
        pytest.skip("Not running as root; network tests skipped", )
    return base_injector.claim()


@async_test
async def test_network_create(injector, loop):
    ainjector = injector(AsyncInjector)
    net = await ainjector(Network, name = "testnet")
    net.close()
    

@async_test
async def test_network_veth(injector, loop):
    ainjector = injector(AsyncInjector)
    net = await ainjector(Network, name = "testnet")
    net = await net.access_by(BridgeNetwork)
    ve = net.add_veth('database.hadronindustries.com')
    net.close()
    
