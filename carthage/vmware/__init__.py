import carthage.dependency_injection
from .vm import Vm, VmFolder, VmTemplate
from .image import *
from .network import DistributedPortgroup
from .inventory import VmwareConnection, wait_for_task


@carthage.dependency_injection.inject(
    injector = carthage.dependency_injection.Injector)
def carthage_plugin(injector):
    from ..config import ConfigIterator
    from ..dependency_injection import partial_with_dependencies
    from ..utils import when_needed
    injector.add_provider(DistributedPortgroup, allow_multiple = True)
    injector.add_provider(VmfsDataStore)
    injector.add_provider(Vm)
    injector.add_provider(VmwareConnection)
    image_injector = injector(carthage.dependency_injection.Injector)
    image_injector.add_provider(vm_storage_key, partial_with_dependencies(ConfigIterator, prefix="vmware.image_datastore."), allow_multiple = True)
    injector.add_provider(image_datastore_key, when_needed(NfsDataStore, injector = image_injector))
    
