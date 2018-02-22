import asyncio, logging
from carthage.hadron_layout import database_key
from carthage.dependency_injection import AsyncInjector
from carthage import base_injector, ssh
from carthage.network import Network
from carthage.container import container_image

async def run():

    ainjector = base_injector(AsyncInjector)
    container = await ainjector.get_instance_async(database_key)
    await ainjector.get_instance_async(ssh.SshKey)
    async with container.container_running:
        await container.network_online()
        await asyncio.sleep(1)
        container.ssh(_fg = True)
        

logging.getLogger('carthage.container').setLevel(10)
#logging.getLogger('carthage.dependency_injection').setLevel(10)
logging.basicConfig()
asyncio.get_event_loop().run_until_complete(run())
