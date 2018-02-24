# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import weakref
from sqlalchemy import create_engine
import carthage.hadron_layout
import carthage.config
import carthage.ssh
import carthage.container
from ..dependency_injection import inject, InjectionKey, Injector
from ..ports import ExposedPort
from ..container import Container
from ..network import Network
from ..config import ConfigLayout

from hadron.inventory.admin import models

@inject(
    config_layout = carthage.config.ConfigLayout,
    database = carthage.hadron_layout.database_key,
    ssh_key = carthage.ssh.SshKey,
    ssh_origin = carthage.container.ssh_origin)
class RemotePostgres(ExposedPort):

    def __init__(self, config_layout, database, ssh_key, ssh_origin):
        # We don't actually need the ssh key ourselves, but we want it
        # injected to make sure it has been constructed, because we
        # plan to call ssh in a non-async context, and
        # UnsatisfactoryDependency will be raised if the key has not
        # previously been constructed.
        super().__init__(config_layout = config_layout,
                         dest_addr = 'unix-connect:/var/run/postgresql/.s.PGSQL.5432',
                         ssh_origin = ssh_origin
        )
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
