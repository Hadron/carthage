import io, os
from .dependency_injection import inject, AsyncInjector, Injector, AsyncInjectable, Injectable
from .config import ConfigLayout
from .image import SetupTaskMixin, setup_task
from . import sh
from .utils import memoproperty


@inject(config_layout = ConfigLayout)
class SshKey(AsyncInjectable, SetupTaskMixin):

    def __init__(self, config_layout):
        super().__init__()
        self.config_layout = config_layout
        
    async def async_ready(self):
        await self.run_setup_tasks()
        return self

    @setup_task('gen-key')
    async def generate_key(self):
        no_passphrase = io.StringIO("")
        os.makedirs(self.config_layout.state_dir, exist_ok = True)
        await sh.ssh_keygen(f = self.key_path,
                            _in = no_passphrase,
                            _bg = True,
                            _bg_exc = False)
        
    @memoproperty
    def key_path(self):
        return self.config_layout.state_dir+'/ssh_key'

    @memoproperty
    def stamp_path(self):
        return self.config_layout.state_dir

    @memoproperty
    def ssh(self):
        return sh.ssh.bake(i = self.key_path)

    @memoproperty
    def contents(self):
        with open(self.key_path, "rt") as f:
            return f.read()
        

@inject(config_layout = ConfigLayout,
        ssh_key = SshKey)
class AuthorizedKeysFile(Injectable):

    def __init__(self, config_layout, ssh_key):
        self.path = config_layout.state_dir+'/authorized_keys'
        environ = os.environ.copy()
        environ['PYTHONPATH'] = config_layout.hadron_operations
        sh.python3('-mhadron.inventory.config.default_keys',
                   _env = environ,
                   _out = config_layout.hadron_operations + '/ansible/output/authorized_keys.default')
        with open(config_layout.hadron_operations+"/ansible/output/authorized_keys.default", "rt") as in_keys:
            with open(self.path, "wt") as f:
                f.write(in_keys.read())
                f.write(ssh_key.contents)
                
