# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, time
import os

from ssl import create_default_context
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
from ..dependency_injection import *
from ..setup_tasks import SetupTaskMixin
from ..config import ConfigLayout, config_defaults, config_key
from ..setup_tasks import SetupTaskMixin, setup_task, SkipSetupTask
from ..utils import memoproperty
from .. import ConfigLayout
from . import vm
import carthage.ansible

vmware_config = config_key('vmware')

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

class VmwareStampable(SetupTaskMixin, AsyncInjectable):
    stamp_type = None #: Override with type of object for which stamp is created
    folder = None

    @memoproperty
    def stamp_path(self):
        state_dir = self.config_layout.state_dir
        p = os.path.join(state_dir, "vmware_stamps", self.stamp_type)
        if self.folder:
            p = os.path.join(p, self.folder.name)
        p = os.path.join(p, self.name)
        p += ".stamps"
        os.makedirs(p, exist_ok = True)
        return p

@inject(config = ConfigLayout)
class VmwareConnection(Injectable):

    def __init__(self, config):
        self.config = config.vmware
        ssl_context = create_default_context()
        if self.config.validate_certs is False:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = 0
        self.connection = None
        self.connection = SmartConnect(host=self.config.hostname, user=self.config.username, pwd=self.config.password,
                          sslContext = ssl_context)
        self.content = self.connection.content

    def close(self):
        if self.connection:
            Disconnect(self.connection)
            self.connection = None

    def __del__(self):
        self.close()

@inject(config_layout = ConfigLayout,
        injector = Injector,
        connection = VmwareConnection)
class VmwareFolder(VmwareStampable):

    #: Override with folder Kind
    kind = NotImplemented

    def __init__(self, name, *, config_layout, injector, connection):
        self.name = name
        self.injector = injector.copy_if_owned().claim()
        self.config_layout = config_layout
        self.connection = connection
        self.ainjector = injector(AsyncInjector)
        self.inventory_object = None
        self.stamp_type = f"{self.kind}_folder"
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
        else: d['folder_type'] = self.kind
        await self.ainjector(carthage.ansible.run_play,
            [carthage.ansible.localhost_machine],
            [{'vcenter_folder': d}])
        await self.ainjector(self._find_inventory_object)

    @create_folder.invalidator()
    async def create_folder(self):
        await self._find_inventory_object()
        return self.inventory_object

    async def _find_inventory_object(self):
        self.inventory_object = self.connection.content.searchIndex.FindByInventoryPath \
            (f"{self.config_layout.vmware.datacenter}/{self.kind}/{self.name}")

    @memoproperty
    def inventory_view(self):
        return self.injector(VmInventory, folder=self)

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.name}>"

@inject(folder = VmwareFolder, connection = VmwareConnection)
class VmInventory(Injectable):

    def __init__(self, *, folder, connection):
        self.view = connection.content.viewManager.CreateContainerView(
            folder.inventory_object,
            [vim.VirtualMachine], True)

    def find_by_name(self, name):
        for v in self.view.view:
            if v.name == name: return v
        return None

def wait_for_task(task):
    loop = asyncio.get_event_loop()
    def callback():
        while  task.info.state not in ('success', 'error'):
            time.sleep(1)
        if task.info.state == 'error':
                raise task.info.error
    return loop.run_in_executor(None, callback)
