# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, logging
from carthage.hadron_layout import database_key
from carthage.dependency_injection import AsyncInjector, InjectionKey
from carthage import base_injector, ssh
from carthage.network import Network
from carthage.container import container_image, Container
from carthage.hadron.database import RemotePostgres
from carthage.hadron import build_database, hadron_vm_image
from sqlalchemy.orm import Session
from carthage.machine import ssh_origin
import carthage.ssh

async def run():

    ainjector = base_injector(AsyncInjector)
    asyncio.ensure_future(ainjector(hadron_vm_image))
    container = await ainjector.get_instance_async(database_key)
    await ainjector.get_instance_async(ssh_origin)
    await ainjector.get_instance_async(carthage.ssh.SshKey)
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
