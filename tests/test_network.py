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
    
