import weakref
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
                         config_layout = config_layout,
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
    session.commit()
    pg.close()
    
