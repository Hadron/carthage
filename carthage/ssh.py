import io, os
from .dependency_injection import inject, AsyncInjector, Injector, AsyncInjectable, Injectable, InjectionKey
from .config import ConfigLayout
from .image import SetupTaskMixin, setup_task
from . import sh
from .utils import memoproperty, when_needed


@inject(config_layout = ConfigLayout,
        ainjector = AsyncInjector)
class SshKey(AsyncInjectable, SetupTaskMixin):

    def __init__(self, config_layout, ainjector):
        super().__init__()
        self.config_layout = config_layout
        self.ainjector = ainjector
        
    async def async_ready(self):
        await self.run_setup_tasks()
        self.agent = await self.ainjector(ssh_agent, key = self)
        del self.ainjector
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
        return sh.ssh.bake(_env = self.agent.agent_environ)

    @memoproperty
    def pubkey_contents(self):
        with open(self.key_path+".pub", "rt") as f:
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
                f.write(ssh_key.pubkey_contents)
                

@inject(
    config_layout = ConfigLayout,
    key = SshKey)
class SshAgent(Injectable):

    def __init__(self, config_layout, key):
        state_dir = config_layout.state_dir
        auth_sock = os.path.join(state_dir, "ssh_agent")
        try: os.unlink(auth_sock)
        except FileNotFoundError: pass
        self.process = sh.ssh_agent('-a', auth_sock,
                                    '-D', _bg = True)
        self.auth_sock = auth_sock
        sh.ssh_add(key.key_path, _env = self.agent_environ)
        

    @memoproperty
    def agent_environ(self):
        env = os.environ.copy()
        env['SSH_AUTH_SOCK'] = self.auth_sock
        return env

ssh_agent = when_needed(SshAgent)
