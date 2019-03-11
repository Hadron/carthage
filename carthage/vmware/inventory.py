# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, datetime, time, os

from ssl import create_default_context
from pyVim.connect import SmartConnect, Disconnect
from pyVmomi import vim
from ..dependency_injection import *
from ..setup_tasks import SetupTaskMixin
from ..config import ConfigLayout, config_defaults, config_key
from ..setup_tasks import SetupTaskMixin, setup_task, SkipSetupTask
from ..utils import memoproperty, when_needed
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

class VmwareMarkable(object):

    created = 'com.hadronindustries.carthage.created'

    def set_custom_fields(self, entity):
        field = self.ensure_custom_field(VmwareMarkable.created, vim.ManagedEntity)
        timestamp = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()
        self.set_custom_field(entity, field, timestamp)

    def fetch_custom_field(self, fname):
        content = self.connection.content
        cfm = content.customFieldsManager
        for f in cfm.field:
            if f.name == fname:
                return f
        raise KeyError(fname)

    def ensure_custom_field(self, fname, ftype):
        try:
            return self.fetch_custom_field(fname)
        except KeyError:
            content = self.connection.content
            cfm = content.customFieldsManager
            return cfm.AddFieldDefinition(name=fname, moType=ftype)

    def set_custom_field(self, entity, field, value):
        content = self.connection.content
        cfm  = content.customFieldsManager
        cfm.SetField(entity=entity, key=field.key, value=value)


    def objects_with_field(self, root, field):
        content = self.connection.content
        container = content.viewManager.CreateContainerView(root, [vim.ManagedEntity], True)
        ret = set()
        for obj in container.view:
            for val in obj.customValue:
                if val.key == field.key:
                    ret.add(obj)
        container.Destroy()
        return ret

@inject(config_layout = ConfigLayout,
        injector = Injector,
        connection = VmwareConnection)
class VmwareFolder(VmwareStampable, VmwareMarkable):

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
        super().__init__(self)

    @staticmethod
    def canonicalize(name):
        parts = [x for x in name.split('/') if x != ""]
        return "/".join(parts)


    @setup_task("create_folder")
    async def create_folder(self):

    
        self.inventory_object = await self._find_inventory_object()
        if self.inventory_object is not None: return

        parent, sep, tail = self.name.rpartition('/')
        if sep != "" and parent != "":
            parent = self.canonicalize(parent)
            parent_key = InjectionKey(self.__class__, name=parent)
            try: self.parent = await self.ainjector.get_instance_async(parent_key)
            except KeyError:
                base_injector.add_provider(parent_key, when_needed(self.__class__,
                                                                   parent, injector = self.injector))
                self.parent = await self.ainjector.get_instance_async(parent_key)

            pobj = self.parent.inventory_object
        else:
            dc = await self._find(self.config_layout.vmware.datacenter)
            pobj = getattr(dc, self.kind + 'Folder')

        ret = pobj.CreateFolder(tail)
        self.set_custom_fields(ret)

        self.inventory_object = await self._find_inventory_object()
        assert self.inventory_object is not None

    @create_folder.invalidator()
    async def create_folder(self):
        self.inventory_object = await self._find_inventory_object()
        return self.inventory_object

    async def _find(self, s):
        return self.connection.content.searchIndex.FindByInventoryPath(s)

    async def _find_inventory_object(self):
        return await self._find \
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

    def close(self):
        try:
            self.view.Destroy()
            del self.view
        except: pass
        
def wait_for_task(task):
    loop = asyncio.get_event_loop()
    def callback():
        while  task.info.state not in ('success', 'error'):
            time.sleep(1)
        if task.info.state == 'error':
                raise task.info.error
    return loop.run_in_executor(None, callback)
