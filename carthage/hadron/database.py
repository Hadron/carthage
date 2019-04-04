# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, weakref, time
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
import carthage.hadron_layout
import carthage.config
import carthage.ssh
import carthage.container
from ..dependency_injection import inject, InjectionKey, Injector, AsyncInjector
from ..ports import ExposedPort
from ..container import Container
from ..network import Network
from ..config import ConfigLayout

from hadron.inventory.admin import models

@inject(
    config_layout = carthage.config.ConfigLayout,
    database = carthage.hadron_layout.database_key)
class RemotePostgres(ExposedPort):

    def __init__(self, config_layout, database):
        super().__init__(config_layout = config_layout,
                         dest_addr = 'unix-connect:/var/run/postgresql/.s.PGSQL.5432',
                         ssh_origin = database)
        self.engines = weakref.WeakSet()
        time.sleep(0.1)


    def close(self):
        for e in self.engines:
            try: e.close()
            except Exception: pass
        super().close()

        def __del__(self):
            self.close()

    def engine(self, *args, **kwargs):
        engine = create_engine("postgresql://root@localhost:{}/hadroninventoryadmin".format(self.port),
                               *args, **kwargs)
        self.engines.add(engine)
        return engine
    
            
site_network_key = InjectionKey('site-network')

@inject(
    config_layout = ConfigLayout,
    injector = Injector)
class HadronNetwork(Network):

    def __init__(self, model, *, config_layout, injector):
        self.model = model
        self.netid = model.netid
        injector = injector.copy_if_owned()
        injector.claim()
        injector.add_provider(site_network_key, self)
        super().__init__(name = "n{}".format(model.netid),
                         vlan_id = 1000+model.netid,
                         injector = injector)
        
    async def async_ready(self):
        await super().async_ready()
        return self


site_router_key = InjectionKey('site-router')
@inject(ainjector = AsyncInjector)
async def fixup_database(ainjector):
    pg = await ainjector(RemotePostgres)
    session = Session(pg.engine())
    session.query(models.Network).update({
        "extif": "eth0",
        "intif": "eth1"})
    machine_role = session.query(models.Role).filter_by(name = 'machine').one()
    for s in session.query(models.Slot).join(models.Role).filter(models.Role.name == 'apt-server'):
        s.role = machine_role

    session.commit()
    pg.close()
    

@inject(ainjector = AsyncInjector)
async def only_permitted_vms(permitted_vms, *, ainjector):
    permitted_vms = frozenset(permitted_vms)
    pg = await ainjector(RemotePostgres)
    await asyncio.sleep(0.2)
    session = Session(pg.engine())
    for v in session.query(models.VirtualMachine).filter(models.VirtualMachine.vm_type.in_(['8g-hvm-spice', '8g-hvm-headless'])):
        if v.slot.fqdn() not in permitted_vms:
            session.delete(v)
            
    session.commit()
    pg.close()
    
