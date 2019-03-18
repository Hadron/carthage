import carthage.dependency_injection

from .vm import Vm, VmFolder, VmTemplate
from .image import *
from .inventory import VmwareStampable, VmwareManagedObject
from .cluster import VmwareCluster
from .utils import wait_for_task
from .connection import VmwareConnection
from .network import DistributedPortgroup, vmware_trunk_key, DvSwitch
from .host import *
from .folder import *

import carthage.vmware.network as network

@carthage.dependency_injection.inject(
    injector = carthage.dependency_injection.Injector)
def carthage_plugin(injector):
    from ..config import ConfigIterator
    from ..dependency_injection import partial_with_dependencies
    from ..utils import when_needed
    injector.add_provider(DistributedPortgroup, allow_multiple = True)
    injector.add_provider(vmware_trunk_key, network._vmware_trunk)
    injector.add_provider(VmfsDataStore)
    injector.add_provider(DvSwitch)
    injector.add_provider(VmwareConnection)
    image_injector = injector(carthage.dependency_injection.Injector)
    image_injector.add_provider(vm_storage_key, partial_with_dependencies(ConfigIterator, prefix="vmware.image_datastore."), allow_multiple = True)
    injector.add_provider(image_datastore_key, when_needed(NfsDataStore, injector = image_injector))
    
