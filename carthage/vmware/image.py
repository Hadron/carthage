# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import logging
import os
import os.path
from pathlib import Path
from carthage.image import ImageVolume, SetupTaskMixin, setup_task
from carthage.utils import memoproperty
from carthage.dependency_injection import *
from carthage.config import ConfigSchema, ConfigLayout, config_key, ConfigAccessor
from ..config.types import ConfigPath
from .. import sh
from .credentials import vmware_credentials
from .datastore import VmwareDataStore
from .datacenter import VmwareDatacenter


logger = logging.getLogger('carthage.vmware')

#: Injection key used for looking up templates for VMs
image_datastore_key = InjectionKey(VmwareDataStore, role="images")
vm_datastore_key = InjectionKey(VmwareDataStore, role='vm')


@inject_autokwargs(
    config_layout=ConfigLayout,
    store=image_datastore_key
)
class VmdkTemplate(SetupTaskMixin, AsyncInjectable):

    '''
    Produce a VMDK from an image that can be loaded as a template VM.


    :param: image
        A :class:`~carthage.image.ImageVolume` to turn into a VMDK template.


    :param dspath: The base path within the datastore at which images should be stored; if ``None``, taken from ``vmware.image_datastore.path`` in config.


'''

    def __init__(self, image, dspath=None,
                 prefix="",
                 **kwargs):
        self.image = image
        self.dspath = dspath
        super().__init__(**kwargs)
        if dspath is None:
            self.dspath = self.config_layout.vmware.image_datastore.path
            if self.dspath and not self.dspath.endswith('/'):
                self.dspath += '/'
        assert str(image.path).endswith('.raw')
        path = Path(image.path)
        self.prefix = prefix
        if not self.prefix.endswith('/'):
            self.prefix += '/'
        self.paths = [path.stem + ".vmdk", path.stem + "-flat.vmdk"]
        self.path = path.parent
        self.stamp_path = self.image.stamp_path

    def __repr__(self):
        return f"<VMDK for \"{self.image.path}\" datastore={self.store.name}>"

    @setup_task("generate-vmdk")
    async def generate_vmdk(self):
        await self.image.async_become_ready()
        await sh.qemu_img(
            'convert',
            "-Ovmdk",
            "-osubformat=monolithicFlat",
            self.image.path,
            str(self.path / self.paths[0]),
            _bg=True, _bg_exc=False)

        return self

    @generate_vmdk.check_completed()
    def generate_vmdk(self):
        try:
            st = os.stat(self.path / self.paths[0])
            return st.st_mtime
        except FileNotFoundError:
            return False

    @setup_task("copy-vmdk")
    async def copy_vmdk(self):
        store = self.store
        for p in self.paths:
            await store.copy_in(self.path / p, self.dspath + self.prefix + p)

    @memoproperty
    def disk_path(self):
        return f'[{self.store.name}]{self.dspath}{self.prefix}{self.paths[0]}'


class VmwareDatastoreConfig(ConfigSchema, prefix="vmware.datastore"):
    name: str
    #: Path within the data store at which VMs are stored
    path: str = ""
    local_path: ConfigPath = None


class ImageDatastoreConfig(ConfigSchema, prefix="vmware.image_datastore"):
    name: str
    #: Base path within the data store at which images are stored
    path: str = "templates"
    local_path: ConfigPath = None


@inject(injector=Injector)
async def produce_datastore_from_config(prefix, *, injector):
    ainjector = injector(AsyncInjector)
    config = injector(ConfigAccessor, prefix=prefix)
    return await ainjector(VmwareDataStore, name=config.name, local_path=config.local_path)

__all__ = ('image_datastore_key', 'vm_datastore_key',
           'VmdkTemplate',
           'VmwareDataStore')
