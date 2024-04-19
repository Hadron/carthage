# Copyright (C) 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *

import datetime
import time
import os
import types

from pyVmomi import vim, vmodl

from .connection import VmwareConnection
vmware_config = config_key('vmware')
__all__ = "VmwareStampable VmwareManagedObject VmwareNamedObject VmwareSpecifiedObject custom_fields_key default_custom_fields all_objs".split()

#: The provider for this key should be a dictionary mapping field names to functions.  The function takes a VmwareStampable and returns the field value.
custom_fields_key = InjectionKey('vmware.custom_fields_key')


@inject(config=ConfigLayout)
def vmware_dict(config, **kws):
    '''
:returns: A dictionary containing vmware common parameters to pass into Ansible
'''
    vconfig = config.vmware
    d = dict(
        datacenter=vconfig.datacenter,
        username=vconfig.username,
        hostname=vconfig.hostname,
        validate_certs=vconfig.validate_certs,
        password=vconfig.password)
    d.update(kws)
    return d


class NotFound(LookupError):
    pass


@inject_autokwargs(config_layout=ConfigLayout,
                   injector=Injector,
                   connection=VmwareConnection)
class VmwareStampable(SetupTaskMixin, AsyncInjectable):

    def __init_subclass__(cls, *, kind=NotImplemented):
        if kind is not NotImplemented:
            cls.stamp_type = kind

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    @memoproperty
    def stamp_descriptor(self):
        raise NotImplementedError(type(self))

    @memoproperty
    def stamp_path(self):
        p = self.config_layout.state_dir
        p = os.path.join(p, "vmware_stamps", self.stamp_type)
        p = os.path.join(p, *self.stamp_descriptor.lstrip('/').split('/'))
        p += ".stamps"
        os.makedirs(p, exist_ok=True)
        return p


class VmwareManagedObject(VmwareStampable):

    '''Contains a reference to a VMware managed object.'''

    # custom fields
    created = 'com.hadronindustries.carthage.created'

    def __init__(self, *args, parent=None, mob=None, readonly=None, **kwargs):
        '''

        :param parent: the parent Carthage object or alternatively an inventory path of the parent.
        :param mob: manage an existing VMware managed object
        :param readonly: set if it should be readonly; should exist already; should not be removed or changed

        '''

        if isinstance(parent, str):
            self.parent = None
            self.parent_path = self.canonicalize_path(parent)
        else:
            if not isinstance(parent, self.parent_type):
                raise TypeError(f'{type(parent)} is not a valid type to be a parent of {type(self)}')
            self.parent = parent
            if parent is not None:
                self.parent_path = self.parent.vmware_path
        if self.is_root:
            self.parent_path = ""
        if not hasattr(self, 'parent_path'):
            raise TypeError(f"Parent must be set for {type(self)}")
        # If you need to be able to pass in mob without parent or parent path, I
        # know how to do that but it's not yet implemented; ask
        self.mob = mob
        if self.parent and self.mob:
            assert self.mob.parent == self.parent.mob

        self.writable = None if readonly is None else not readonly
        super().__init__(*args, **kwargs)

    @memoproperty
    def stamp_descriptor(self):
        return self.vmware_path

    #: False if this object should have a parent
    is_root = False

    @setup_task("Construct object")
    async def construct(self):
        if not self.mob:
            if not self.writable:
                raise NotFound(f'{type(self)} with path {self.vmware_path} does not exist')
        await self.do_create()
        self.mob = self._find_from_path()
        if self.mob is None:
            raise RuntimeError(f'constructed object, but could not find it at {self.vmware_path}')
        self.set_custom_fields()

    @construct.check_completed()
    async def construct(self):
        if not self.mob:
            self.mob = self._find_from_path()
        await self._find_parent()
        if not self.mob:
            return False
        v = self.get_field_value(self.created)
        if v is None:
            return True  # We don't know the dependency
        return datetime.datetime.fromisoformat(v).timestamp()

    async def _find_parent(self):
        if self.parent or self.is_root:
            return
        parent_key = InjectionKey(self.parent_type, path=self.parent_path)
        try:
            self.parent = await self.ainjector.get_instance_async(parent_key)
            return
        except KeyError:
            connection_injector = self.ainjector.injector_containing(VmwareConnection)
            connection_injector.add_provider(parent_key, when_needed(self.parent_type, name=self.parent_path))
            self.parent = await self.ainjector.get_instance_async(parent_key)

    async def do_create(self):
        raise NotImplementedError

    def children(self, objtypes, recursive=True):
        if self.mob is None:
            return
        vm = self.connection.content.viewManager
        container = None
        try:
            container = vm.CreateContainerView(self.mob, objtypes, recursive)
            for ref in container.view:
                yield ref
        finally:
            if container is not None:
                container.Destroy()

    @staticmethod
    def canonicalize_path(path):
        parts = [x for x in path.split('/') if x != '']
        return '/' + '/'.join(parts)

    @memoproperty
    def vmware_path(self):
        raise NotImplementedError

    async def get_permissions(self):
        am = self.connection.content.authorizationManager
        return am.RetrieveEntityPermissions(self.mob, inherited=True)

    async def add_permissions(self, permissions):
        raise NotImplementedError

    async def set_permissions(self, permissions):
        am = self.connection.content.authorizationManager
        vmware = [vim.AuthorizationManager.Permission(
            entity=self.mob,
            principal=permission.principal.principal,
            group=permission.principal.group,
            roleId=permission.role.mob.roleId,
            propagate=permission.propagate
        )
            for permission in permissions]
        am.SetEntityPermissions(self.mob, vmware)

    async def _find_by_name(self, name, vimtype):

        vm = self.connection.content.viewManager
        container = vm.CreateContainerView(self.connection.content.rootFolder, [vimtype], True)
        found = None
        for ref in container.view:
            if ref.name == name:
                found = ref
                break
        container.Destroy()
        return found

    def _find_from_path(self):
        find = self.connection.content.searchIndex.FindByInventoryPath
        ret = find(self.vmware_path)
        return ret

    def set_custom_fields(self):
        entity = self.mob
        fields = self.injector.get_instance(custom_fields_key)
        for name, val_func in fields.items():
            field = self._ensure_custom_field(name, vim.ManagedEntity)
            self.set_custom_field(field, val_func(self))

    def _fetch_custom_field(self, fname):
        content = self.connection.content
        cfm = content.customFieldsManager
        for f in cfm.field:
            if f.name == fname:
                return f
        raise KeyError(fname)

    def _ensure_custom_field(self, fname, ftype):
        try:
            return self._fetch_custom_field(fname)
        except KeyError:
            content = self.connection.content
            cfm = content.customFieldsManager
            if not self.writable:
                # This isn't exactly right since it looks at writable
                # for the object not the overall connection
                raise ValueError(f'unable to write field {fname} because object is read-only')
            return cfm.AddFieldDefinition(name=fname, moType=ftype)

    def set_custom_field(self, field, value):
        if self.mob is None:
            raise RuntimeError('unable to set fields on null object')
        if isinstance(field, str):
            field = self._fetch_custom_field(field)
        content = self.connection.content
        cfm = content.customFieldsManager
        cfm.SetField(entity=self.mob, key=field.key, value=value)

    def get_field_value(self, field):
        '''Return the vmware custom field value or None if not set
        '''
        if isinstance(field, str):
            try:
                field = self._fetch_custom_field(field)
            except KeyError:
                return None
        for val in self.mob.customValue:
            if (val.key == field.key) and (val.value != ''):
                return val.value
        return None

    @staticmethod
    def _parent_path_from_mob(mob):
        parts = []
        while mob:
            parts.append(mob.name)
            if isinstance(mob, vim.Datacenter):
                mob = None
            else:
                mob = mob.parent
        return "/" + "/".join(reversed(parts))


class VmwareNamedObject(VmwareManagedObject):

    def __init__(self, name=None, *args, **kwargs):
        parent = kwargs.get('parent', None)
        if name is None:
            raise ValueError(f'must specify name')
        if parent and name.startswith('/'):
            raise TypeError("Cannot specify both a parent and a name containing a full path")
        elif parent and '/' in name:
            parent_add, sep, name = name.rpartition('/')
            if not isinstance(parent, str):
                parent = parent.vmware_path
            kwargs['parent'] = parent + '/' + parent_add
        elif parent is None and '/' in name:
            kwargs['parent'], sep, name = name.rpartition('/')
        if 'mob' in kwargs and name is None:
            name = kwargs['mob'].name
        #: The name of the object
        self.name = name
        super().__init__(*args, **kwargs)

    @memoproperty
    def vmware_path(self):
        return f'{self.parent_path}/{self.full_name}'

    @property
    def full_name(self):
        '''Returns the full name of the object.  The typical difference between this and name is that full_name includes the container_prefix for objects where that is necessary
'''
        return self.name

    def __repr__(self):
        return f"<{self.__class__.__name__}: {self.vmware_path}>"


class VmwareSpecifiedObject(VmwareNamedObject):

    #: Class variable containing set of `ConfigSpecStages` for this type of object.  Copied on any subclass that has *ConfigSpecStages*
    config_stages = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    async def build_config(self, mode, oconfig=None, stagefilter=None):
        if stagefilter is None:
            def stagefilter(cs): return mode in cs.mode
        ainjector = self.ainjector
        bag = types.SimpleNamespace(mode=mode)
        stages = []
        for cs in self.__class__.config_stages:
            if not stagefilter(cs):
                continue
            stages.append(cs(obj=self, bag=bag))
        config = self.config_spec_class()
        if self.mob:
            config.version = self.mob.config.version
        for s in stages:
            if oconfig:
                s.oconfig = oconfig
            await ainjector(s.apply_config, config)
        return config


def all_objs(content, root, objtype):
    vm = content.viewManager
    container = vm.CreateContainerView(root, objtype, True)
    for ref in container.view:
        yield ref
    container.Destroy()


def created_current_time(_):
    return datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()


default_custom_fields = {
    VmwareManagedObject.created: created_current_time}
