# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *

from .folder import VmwareFolder, HostFolder
from .inventory import VmwareSpecifiedObject

__all__ = ['VmwareHost']

@inject(**VmwareSpecifiedObject.injects)
class VmwareHost(VmwareSpecifiedObject, kind='host'):
    from .cluster import VmwareCluster
    parent_type = (VmwareCluster, HostFolder)
