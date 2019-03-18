from carthage import *

from .folder import VmwareFolder
from .inventory import VmwareSpecifiedObject

__all__ = ['HostFolder', 'VmwareHost']

@inject(**VmwareFolder.injects)
class HostFolder(VmwareFolder, kind='host'):


    pass

@inject(**VmwareSpecifiedObject.injects)
class VmwareHost(VmwareSpecifiedObject, kind='host'):
    parent_type = HostFolder
    
    pass

