# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import carthage.ansible
import os.path
from ..machine import Machine
from ..dependency_injection import *
from ..config import ConfigLayout, config_defaults, config_key
from ..image import SetupTaskMixin, setup_task
from ..utils import memoproperty
from .image import VmwareDataStore


config_defaults.add_config({
    'vmware': {
        'datacenter': None,
        'folder': 'carthage',
        'vm_folder': None,
        'hardware': {
            'boot_firmware': 'efi',
            'version': 14,
            'memory': 4096,
            'disk': 25,
            },
        }})
vmware_config = config_key('vmware')

class VmwareStampable(SetupTaskMixin, AsyncInjectable):
    stamp_type = None #: Override with type of object for which stamp is created
    folder = None


    @memoproperty
    def stamp_path(self):
        state_dir = self.config_layout.state_dir
        p = os.path.join(state_dir, "vmware_stamps", self.stamp_type)
        if self.folder:
            p = os.path.join(p, self.folder)
        p = os.path.join(p, self.name)
        p += ".stamps"
        os.makedirs(p, exist_ok = True)
        return p


@inject(config = vmware_config)
def vmware_dict(config, **kws):
    '''
:returns: A dictionary containing vmware common parameters to pass into Ansible
'''
    d = dict(
        datacenter = config.datacenter,
        username = config.username,
        hostname = config.hostname,
        validate_certs = config.validate_certs,
        password = config.password)
    d.update(kws)
    return d

@inject(config_layout = ConfigLayout,
        injector = Injector)
class VmFolder(VmwareStampable):

    stamp_type = "vm_folder"

    def __init__(self, name, *, config_layout, injector):
        self.name = name
        self.injector = injector.claim()
        self.config_layout = config_layout
        self.ainjector = injector(AsyncInjector)
        super().__init__()

    @setup_task("create_folder")
    async def create_folder(self):
        d = self.injector(vmware_dict)
        d['folder_name'] = self.name
        d['state'] = 'present'
        parent, sep, tail = self.name.rpartition('/')
        if sep != "":
            d['parent_folder'] = parent
            d['folder_name'] = tail
            self.parent = await self.ainjector(self.__class__, parent)
        else: d['folder_type'] = 'vm'
        await self.ainjector(carthage.ansible.run_play,
            [carthage.ansible.localhost_machine],
            [{'vcenter_folder': d}])

    def __repr__(self):
        return f"<VmFolder: {self.name}>"
    

        
            
@inject(
    config = ConfigLayout,
    injector = Injector,
    storage = VmwareDataStore,
    # We add a template VM to the dependencies later avoiding a forward reference
    )
class Vm(Machine):

    def __init__(self, name, template,
                 *, injector, config, storage):
        super().__init__(name, injector, config)
        self.storage = storage

        
