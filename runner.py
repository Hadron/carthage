# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import sys, gc, traceback
import asyncio, logging
from carthage.hadron_layout import database_key
from carthage.dependency_injection import AsyncInjector, InjectionKey
from carthage import base_injector, ssh
from carthage.network import Network
from carthage.container import container_image, Container
from carthage.hadron.database import RemotePostgres
from carthage.hadron import build_database, hadron_vm_image
from sqlalchemy.orm import Session
from carthage.machine import ssh_origin, Machine
import carthage.ssh
machines = []

async def queue_worker():
    global machines
    while True:
        name = await machine_queue.get()
        try:
            m_new = await ainjector.get_instance_async(InjectionKey(Machine, host = name))
            await m_new.start_machine()
            machines.append(m_new)
        except Exception:
            print('Error Creating {}'.format(name))
            traceback.print_exc()
        
        
async def run():

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
        session.close()
        loop.create_task(queue_worker())
        loop.create_task(queue_worker())
        def callback():
            container.ssh("-A", _fg = True)
        await loop.run_in_executor(None, func = callback)
            
        global machines
        for m in machines:
            m.close()
    del machines
            

ainjector = base_injector(AsyncInjector)

#logging.getLogger('carthage.container').setLevel(7)
#logging.getLogger('carthage.dependency_injection').setLevel(10)
logging.basicConfig(level = 'INFO')
loop = asyncio.get_event_loop()
machine_queue = asyncio.Queue()
with open(sys.argv[1]) as f:
    for m in f.readlines():
        machine_queue.put_nowait(m.strip())

loop.run_until_complete(run())
del base_injector._providers
gc.collect()
