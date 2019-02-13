import logging, os
from ..image import ImageVolume, SetupTaskMixin, setup_task
from ..utils import memoproperty
from ..dependency_injection import *
from ..config import config_defaults, ConfigLayout, config_key
from .. import sh
from .credentials import vmware_credentials

class VmwareDataStore(Injectable): pass

logger = logging.getLogger('carthage.vmware')
        
#: Injection key used for looking up templates for VMs
image_datastore_key = InjectionKey(VmwareDataStore, role = "images")

@inject(injector = Injector,
        config_layout = ConfigLayout,
        store = image_datastore_key
        )
class VmdkTemplate(SetupTaskMixin, AsyncInjectable):

    '''
    Produce a VMDK from an image that can be loaded as a template VM.

    
    :param: image
        A :class:`~carthage.image.ImageVolume` to turn into a VMDK template.

'''

    def __init__(self, image, store, *, injector, config_layout):
        self.injector = injector.copy_if_owned().claim()
        self.ainjector = self.injector(AsyncInjector)
        self.image = image
        self.store = store
        super().__init__()
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

    
config_defaults.add_config({'vmware': {
    'datastore': {
        'name': None,
        'path': "",
        'local_path': None,
        },
"image_datastore": {
"name": None,
"path": None,
"local_path": None},

    }})

vm_storage_key = config_key("vmware.datastore")


@inject(
    injector = Injector,
    storage = vm_storage_key,
    credentials = vmware_credentials)
class NfsDataStore(VmwareDataStore):

    '''
    Represents an NFS data store.  The data store can be accessed locally by an already mounted or local path.  Alternatively it can be accessed via scp.  For scp access, set the *local_path* parameter in ``vmware.storage`` configuration to *host*\ :\ *path*.

    '''

    def __init__(self, injector, storage, credentials):
        self.injector = injector
        self.credentials = credentials
        self.storage = storage
        for k in ('name', 'path', 'local_path'):
            assert getattr(storage, k), \
                "You must configure vmware.datastore.{}".format(k)
        self.name = self.storage.name
        self.path = self.storage.path

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
            raise NotImplementedError()

@inject(config = vm_storage_key)
class VmfsDataStore(VmwareDataStore, Injectable):

    def __init__(self, config):
        self.name = config.name
        self.path = config.path


__all__ = ('VmfsDataStore', 'NfsDataStore', 'image_datastore_key', 'vm_storage_key',
           'VmdkTemplate',
           'VmwareDataStore')
