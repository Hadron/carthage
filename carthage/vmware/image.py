# Copyright (C) 2018, 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import logging, os, os.path, requests
from ..image import ImageVolume, SetupTaskMixin, setup_task
from ..utils import memoproperty
from ..dependency_injection import *
from ..config import ConfigSchema, ConfigLayout, config_key
from ..config.types import ConfigPath
from .. import sh
from .credentials import vmware_credentials
from .datastore import VmwareDataStore
from .datacenter import VmwareDatacenter


logger = logging.getLogger('carthage.vmware')
        
#: Injection key used for looking up templates for VMs
image_datastore_key = InjectionKey(VmwareDataStore, role = "images")

@inject_autokwargs(
        config_layout = ConfigLayout,
        store = image_datastore_key
        )
class VmdkTemplate(AsyncInjectable, SetupTaskMixin):

    '''
    Produce a VMDK from an image that can be loaded as a template VM.

    
    :param: image
        A :class:`~carthage.image.ImageVolume` to turn into a VMDK template.

'''

    def __init__(self, image, **kwargs):
        self.image = image
        super().__init__(**kwargs)
        assert image.path.endswith('.raw')
        base_path = image.path[:-4]
        self.paths = (base_path+'.vmdk',
                      base_path+"-flat.vmdk")
        self.stamp_path = self.image.stamp_path

    def __repr__(self):
        return f"<VMDK for \"{self.image.path}\" datastore={self.store.name}>"
    


    @setup_task("generate-vmdk")
    async def generate_vmdk(self):
        await sh.qemu_img(
            'convert',
            "-Ovmdk",
            "-osubformat=monolithicFlat",
            self.image.path,
            self.paths[0],
            _bg = True, _bg_exc = False)

        
        return self

    @generate_vmdk.check_completed()
    def generate_vmdk(self):
        try:
            st = os.stat(self.paths[0])
            return st.st_mtime
        except FileNotFoundError: return False
        

    @setup_task("copy-vmdk")
    async def copy_vmdk(self):
        store = self.store
        return await store.copy_in(self.paths)

    @memoproperty
    def disk_path(self):
        return f'[{self.store.name}]{self.store.path}/{self.image.name}.vmdk'

    
class VmwareDatastoreConfig(ConfigSchema, prefix = "vmware.datastore"):
    name: str
    path: str = ""
local_path: ConfigPath

class ImageDatastoreConfig(ConfigSchema, prefix = "vmware.image_datastore"):
    name: str
    path: str = ""
    local_path: ConfigPath
    



vm_storage_key = config_key("vmware.datastore")


@inject(
    storage = vm_storage_key,
    data_center = VmwareDatacenter,
    credentials = vmware_credentials,
    )
class NfsDataStore(VmwareDataStore):

    '''
    Represents an NFS data store.  The data store can be accessed locally by an already mounted or local path.  Alternatively it can be accessed via scp.  For scp access, set the *local_path* parameter in ``vmware.storage`` configuration to *host*\ :\ *path*.

    '''

    def __init__(self, storage, data_center, **kwargs):
        self.storage = storage
        self.data_center = data_center
        for k in ('name', 'path', 'local_path'):
            assert getattr(storage, k), \
                "You must configure vmware.datastore.{}".format(k)
        name = self.storage.name
        self.path = self.storage.path
        super().__init__(name = name, **kwargs)
        

    #: List of ssh options to use when contacting a remote host
    ssh_opts = ('-oStrictHostKeyChecking=no', )
    
    @memoproperty
    def vmdk_path(self):
        if self.ssh_host:
            config = self.injector(ConfigLayout)
            return os.path.join(config.vm_image_dir, "vmdk")
        return self.storage.local_path

    @memoproperty
    def ssh_host(self):
        host, sep, path = self.storage.local_path.partition(":")
        if sep != "":
            return host
        

    async def makedirs(self):
        if self.ssh_host:
            host, sep, folder = self.storage.local_path.partition(":")
            folder = folder.replace('"', '\\"')
            await sh.ssh(self.ssh_opts,
                         host, "mkdir", "-p",
                         f'"{folder}"',
                         _bg = True,
                         _bg_exc = False)
        else:
            os.makedirs(self.storage.local_path)

    def upload_file(self, path):
        basename = os.path.basename(path)
        logger.info( f'Uploading {basename} to {self.name}')
        params = dict(
            dsName = self.name,
            dcPath = self.data_center.name)
        verify = self.config_layout.vmware.validate_certs
        host = self.config_layout.vmware.hostname
        url = f'https://{host}:443/folder/{self.path}/{basename}'
        with open(path, "rb") as f:
            resp = requests.put(url, params = params,
                                data = f, auth=self.auth_tuple,
                                verify = verify)
            if resp.status_code >= 400:
                raise ValueError(f'Uploading {basename} failed: {resp.status_code}')
            
            
    async def copy_in(self, paths):
        '''
        Copy files into the datastore

        For each element of *paths*, copy that file into *self.local_path*. Recursive folder structure is not preserved.

        '''
        if self.ssh_host:
            local_path = self.storage.local_path
            await self.makedirs()
            for p in paths:
                logger.debug(f'copying {p} to {local_path}')
                await sh.scp(self.ssh_opts, p, local_path,
                             _bg = True, _bg_exc = False)
        else:
            for p in paths:
                self.upload_file(p)

        
    @memoproperty
    def auth_tuple(self):
        return (self.config_layout.vmware.username, self.config_layout.vmware.password)
    

@inject(storage = vm_storage_key,
)
class VmfsDataStore(VmwareDataStore):

    def __init__(self, storage, **kwargs):
        name = storage.name
        self.path = storage.path
        super().__init__(name = name, **kwargs)
        


__all__ = ('VmfsDataStore', 'NfsDataStore', 'image_datastore_key', 'vm_storage_key',
           'VmdkTemplate',
           'VmwareDataStore')
