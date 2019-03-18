from carthage import *

from .folder import VmwareFolder, HostFolder
from .inventory import VmwareSpecifiedObject

__all__ = ['VmwareHost']

@inject(**VmwareSpecifiedObject.injects)
class VmwareHost(VmwareSpecifiedObject, kind='host'):
    from .cluster import VmwareCluster
    parent_type = (VmwareCluster, HostFolder)
