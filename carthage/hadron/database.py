import weakref
from sqlalchemy import create_engine
import carthage.hadron_layout
import carthage.config
import carthage.ssh
import carthage.container
from ..dependency_injection import inject
from ..ports import ExposedPort

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
    
            
