# Copyright (C) 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import inspect
def get_caller():
    for sf in inspect.stack():
        if sf.filename.startswith("<"): continue
        if sf.filename.endswith("/carthage/vm/__init__.py"): continue
        return (sf.filename, sf.lineno)
file, line = get_caller()
import warnings
warnings.filterwarnings("default", category=DeprecationWarning, module=__name__)
warnings.warn(f"\n\n\
=============================================\n\
carthage.vm has migrated to carthage.libvirt.\n\
File: '{file}' has imported carthage.vm at line: {line}\n\
Please import carthage.libvirt in the future.\n\
=============================================\n\
", category=DeprecationWarning)

from carthage.libvirt import *
from carthage.libvirt.base import vm_image_key, LibvirtCreatedImage
