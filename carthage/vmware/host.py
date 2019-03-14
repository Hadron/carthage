from carthage import *

from .folder import VmwareFolder
from .inventory import VmwareSpecifiedObject

__all__ = ['HostFolder', 'VmwareHost']

@inject(**VmwareSpecifiedObject.injects)
class VmwareHost(VmwareSpecifiedObject, kind='host'):
    pass

@inject(**VmwareFolder.injects)
class HostFolder(VmwareFolder, kind='host'):


    pass
