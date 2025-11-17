# Copyright (C) 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.dependency_injection import InjectionKey
from carthage.machine import BaseCustomization
from carthage.modeling import ImageRole
from carthage.utils import memoproperty

from .base import LibvirtCreatedImage


__all__ = []

class LibvirtImageModel(LibvirtCreatedImage, ImageRole):

    '''
    A :class:`carthage.libvirt.LibvirtCreatedImage`  that is a modeling class, so modeling language constructs work.  In addition, any customization in the class is included in the default *vm_customizations*.
    '''

    disk_cache = 'unsafe' #Volume is destroyed on failure
    @classmethod
    def our_key(cls):
        return InjectionKey(LibvirtCreatedImage, name=cls.name)

    @memoproperty
    def vm_customizations(self):
        return [x[1] for x in self.injector.filter_instantiate(
            BaseCustomization,
            ['description'], stop_at=self.injector)]
__all__ += ["LibvirtImageModel"]
