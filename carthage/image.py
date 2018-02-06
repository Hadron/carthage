# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import os.path
from .dependency_injection import Injector, AsyncInjectable, inject
from .config import ConfigLayout
from . import sh
from .utils import possibly_async
@inject(config_layout = ConfigLayout)
class BtrfsVolume(AsyncInjectable):

    def __init__(self, config_layout, name, clone_from = None):
        self.config_layout = config_layout
        self._name = name
        self._path = os.path.join(config_layout.image_dir, name)
        self.clone_from = clone_from

    @property
    def name(self): return self._name

    @property
    def path(self):
        if self._path is None:
            raise RuntimeError("This volume is closed")
        return self._path

    def __repr__(self):
        return "<BtrfsVolume path={}>".format(self.path)

    def close(self):
        if self._path is None: return
        if self.config_layout.delete_volumes:
            sh.btrfs('subvolume', 'delete', self.path, _bg = True)
        self._path = None

    def __del__(self): self.close()
    
    async def async_ready(self):
        if os.path.exists(self.path):
            try: sh.btrfs("subvolume", "show", self.path)
            except sh.ErrorReturnCode:
                raise RuntimeError("{} is not a btrfs subvolume but already exists".format(self.path))
            # If we're here it is a btrfs subvolume
            await possibly_async(self.check_volume())
            return self
        # directory does not exist
        if not self.clone_from:
            await sh.btrfs('subvolume', 'create',
                       self.path,
                       _bg = True, _bg_exc = False)
        else:
            await sh.btrfs('subvolume', 'snapshot', self.clone_from.path, self.path,
                           _bg = True, _bg_exc = False)
        await possibly_async(self.populate_volume())
        return self

    def check_volume(self):
        "When the volume alreday exists, check and make sure it is valid.; may be async"
        pass

    async def populate_volume(self):
        "Populate a new volume; called both for cloned and non-cloned volumes"
        pass


