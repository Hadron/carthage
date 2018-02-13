import asyncio
from carthage.hadron import HadronImageVolume
from carthage.dependency_injection import AsyncInjector
from carthage import base_injector

async def run():
    ainjector = base_injector(AsyncInjector)
    volume = await ainjector(HadronImageVolume)

asyncio.get_event_loop().run_until_complete(run())
