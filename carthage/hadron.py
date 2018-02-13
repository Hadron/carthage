import sys
from .image import ImageVolume, setup_task
from .container import Container, container_volume, container_image
from .dependency_injection import inject, Injector, AsyncInjectable, AsyncInjector
from .config import ConfigLayout
from . import sh

@inject(
    config_layout = ConfigLayout,
    injector = Injector
    )
class HadronImageVolume(ImageVolume):

    def __init__(self, injector, config_layout):
        super().__init__(config_layout = config_layout, name = "base-hadron")
        self.injector = injector
        
    @setup_task('hadron_packages')
    async def setup_hadron_packages(self):
        ainjector = self.injector(AsyncInjector)
        ainjector.add_provider(container_volume, self)
        ainjector.add_provider(container_image, self)
        container = await ainjector(Container, name = self.name)
        try:
            await container.start_container( '--bind-ro='+self.config_layout.hadron_operations+":/hadron-operations")
            await container.shell("/usr/bin/apt",
                                  "install", "-y", "ansible",
                                  _bg = True,
                                  _bg_exc = False)
            await container.shell("/usr/bin/ansible-playbook",
                                  "-clocal",
                                  "-ehadron_os=ACES",
                                                                    "-ehadron_track=proposed",
                                  "-ehadron_release=unstable",
                                  "-eaces_apt_server=apt-server.aces-aoe.net",
                                  "-i/hadron-operations/ansible/localhost-debian.txt",
                                  "/hadron-operations/ansible/commands/hadron-packages.yml",
                                  _bg = True,
                                  _bg_exc = False,
                                  _out = sys.stdout,
                                  _err_to_out = True)
        finally:
            await container.stop_container()
            
