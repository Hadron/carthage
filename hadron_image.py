import asyncio
from carthage.hadron import HadronImageVolume
from carthage.dependency_injection import AsyncInjector
from carthage import base_injector
from carthage.network import Network

async def run():

    ainjector = base_injector(AsyncInjector)
    net = await ainjector(Network, "brint", delete_bridge = False)
    base_injector.add_provider(net)
    volume = await ainjector(HadronImageVolume)

asyncio.get_event_loop().run_until_complete(run())
