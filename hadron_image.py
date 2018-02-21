import asyncio, logging
from carthage.hadron_layout import database_key
from carthage.dependency_injection import AsyncInjector
from carthage import base_injector
from carthage.network import Network
from carthage.container import container_image

async def run():

    ainjector = base_injector(AsyncInjector)
    container = await ainjector.get_instance_async(database_key)

logging.getLogger('carthage.container').setLevel(10)
#logging.getLogger('carthage.dependency_injection').setLevel(10)
logging.basicConfig()
asyncio.get_event_loop().run_until_complete(run())
