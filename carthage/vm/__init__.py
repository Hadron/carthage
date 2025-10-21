# Copyright (C) 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import warnings
warnings.filterwarnings("default", category=DeprecationWarning, module=__name__)
warnings.warn("\n\n\
=============================================\n\
carthage.vm has migrated to carthage.libvirt.\n\
Please import carthage.libvirt in the future.\n\
=============================================\n\
", category=DeprecationWarning)

from carthage.libvirt import *
from carthage.libvirt.base import vm_image_key, LibvirtCreatedImage
