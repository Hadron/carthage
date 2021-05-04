import contextlib
from .machine import Machine
from .dependency_injection import *
from . import sh
from .utils import memoproperty
from .setup_tasks import SetupTaskMixin

class LocalMachine(Machine, SetupTaskMixin):

    '''A machine representing the node on which carthage is running.
    '''

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.running = True
        
    ip_address = "127.0.0.1"

    async def async_ready(self):
        await self.resolve_networking()
        await self.run_setup_tasks()
        await super().async_ready()

    @contextlib.asynccontextmanager
    async def filesystem_access(self):
        yield "/"

    async def start_machine(self):
        await self.start_dependencies()
        await super().start_machine()
        return

    async def stop_machine(self):
        raise NotImplementedError("Stopping localhost may be more dramatic than desired")

    @property
    def shell(self):
        # We don't actually need to enter a namespace, but this provides similar semantics to what we get with containers
        return sh.nsenter.bake()

    @memoproperty
    def stamp_path(self):
        return self.config_layout.state_dir+"/localhost"
    
__all__ = ['LocalMachine']
