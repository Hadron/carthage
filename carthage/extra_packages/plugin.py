from carthage.config import ConfigSchema
from carthage.config.types import ConfigPath
from carthage.dependency_injection import *
from carthage.machine import customization_task
class ExtraPackagesConfig(ConfigSchema, prefix = "extra_packages"):

    architectures: str = "amd64 source"
    components: str = "main contrib non-free proprietary"

    #: Accept any .changes file from this directory
    packages_dir: ConfigPath

    repository_dir: ConfigPath = "{state_dir}/extra_packages_repo"

@inject(injector = Injector)
def carthage_plugin(injector):
    from .repo import ExtraRepository, AddExtraRepo, UpgradeFromRepo
    from carthage.hadron.images import HadronImageMixin
    from carthage.hadron.build_database import RouterMixin, NonRouterMixin
    HadronImageMixin.add_extra_repo = customization_task(
        AddExtraRepo,
        before = HadronImageMixin.setup_hadron_packages)
    for c in (NonRouterMixin, RouterMixin):
        c.upgrade_from_extra_repo = customization_task(
            UpgradeFromRepo, before=RouterMixin.ansible_initial_router)
        c.add_extra_repo = customization_task(
            AddExtraRepo,
            before = c.upgrade_from_extra_repo)
        
    injector.add_provider(ExtraRepository)
    
