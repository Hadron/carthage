import sys
from .images import  HadronVmImage
from ..vmware.image import VmdkTemplate, VmwareDataStore
from ..dependency_injection import AsyncInjector, inject, Injector
from ..utils import when_needed
from ..config import ConfigLayout
from .. import sh
from ..machine import ContainerCustomization, customization_task
from ..setup_tasks import setup_task
from ..container import Container, container_image, container_volume


@inject(config_layout = ConfigLayout,
        injector = Injector)
class HadronVmwareCustomization(ContainerCustomization):
    description = "Customizations for ACES on Vmware"

    @setup_task("install-vm-tools")
    async def install_vm_tools(self):
        await self.container_command("/usr/bin/apt", "-y", "install", "open-vm-tools")


@inject(config_layout = ConfigLayout,
        ainjector = AsyncInjector,
        store = VmwareDataStore)
class HadronVmdkBase(HadronVmImage):

    def __init__(self, *, ainjector, config_layout,
                 store,
                 name = "aces-vmdk", **kwargs):
        super().__init__(**kwargs, name = name,
                         ainjector = ainjector, config_layout = config_layout,
                         path = store.vmdk_path)

    vmware_customization = customization_task(HadronVmwareCustomization)

if __name__ == '__main__':
    from carthage import base_injector
    from asyncio import get_event_loop
    loop = get_event_loop()
    ainjector = base_injector(AsyncInjector)
    from carthage.vmware.image import NfsDataStore
    from carthage.config import inject_config
    inject_config(base_injector)
    base_injector.add_provider(NfsDataStore)
    cl = base_injector(ConfigLayout)
    cl.load_yaml(open(sys.argv[1]).read())
    base = loop.run_until_complete(ainjector(HadronVmdkBase))
    base.close()
    template = loop.run_until_complete(ainjector(VmdkTemplate, image = base))

    
