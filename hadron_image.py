import asyncio, logging
from carthage.hadron_layout import database_key
from carthage.dependency_injection import AsyncInjector, InjectionKey
from carthage import base_injector, ssh
from carthage.network import Network
from carthage.container import container_image, Container
from carthage.hadron.database import RemotePostgres
from carthage.hadron import build_database, hadron_vm_image
from sqlalchemy.orm import Session

async def run():

    ainjector = base_injector(AsyncInjector)
    asyncio.ensure_future(ainjector(hadron_vm_image))
    container = await ainjector.get_instance_async(database_key)
    async with container.container_running:
        await container.network_online()
        pg  = await ainjector(RemotePostgres)
        engine = pg.engine()
        session = Session(engine)
        await ainjector(build_database.provide_networks, session = session)
        container2 = await ainjector.get_instance_async(InjectionKey(Container, host ='router.cambridge-test.aces-aoe.com'))
        async with container2.vm_running:
            container.ssh("-A", _fg = True)
        

#logging.getLogger('carthage.container').setLevel(7)
#logging.getLogger('carthage.dependency_injection').setLevel(10)
logging.basicConfig(level = 'INFO')
asyncio.get_event_loop().run_until_complete(run())
