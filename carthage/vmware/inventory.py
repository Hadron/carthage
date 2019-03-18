from carthage import *

import datetime, time, os

from pyVmomi import vim, vmodl

from .connection import VmwareConnection
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

    def __init_subclass__(cls, *, kind=NotImplemented):
        if kind is not NotImplemented:
            cls.stamp_type = kind


    injects = dict(config_layout = ConfigLayout,
                   injector = Injector,
                   connection = VmwareConnection)

    def __init__(self, *args, config_layout, injector, connection, **kwargs):
        if len(args) != 0: raise ValueError(args)
        self.injector = injector.copy_if_owned().claim()
        self.config_layout = config_layout
        self.connection = connection
        self.ainjector = self.injector(AsyncInjector)
        super().__init__(*args, **kwargs)

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
        if self.is_root: self.parent_path = ""
        if not hasattr(self, 'parent_path'):
            raise TypeError(f"Parent must be set for {type(self)}")
        # If you need to be able to pass in mob without parent or parent path, I know how to do that but it's not yet implemented; ask
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
    
    async def async_ready(self):
        await self.construct()
        return await super().async_ready()

    async def construct(self):
        if not self.mob:
            self.mob = self._find_from_path()
        await self._find_parent()
        if not self.mob:
            if not self.writable:
                raise ValueError(f'{type(self)} with path {self.vmware_path} does not exist')
            await self.do_create()
            self.mob = self._find_from_path()

    async def _find_parent(self):
        if self.parent or self.is_root: return
        parent_key = InjectionKey(self.parent_type, path =self.parent_path)
        try:
            self.parent = await self.ainjector.get_instance_async(parent_key)
            return
        except KeyError:
            connection_injector = self.ainjector.injector_containing(VmwareConnection)
            connection_injector.add_provider(parent_key, when_needed(self.parent_type, name = self.parent_path))
            self.parent = await self.ainjector.get_instance_async(parent_key)
            
    async def do_create(self):
        raise NotImplementedError


    def children(self, objtypes, recursive=True):
        assert self.mob is not None
        vm = self.connection.content.viewManager
        container = vm.CreateContainerView(self.mob, objtypes, recursive)
        for ref in container.view:
            yield ref
        container.Destroy()


    @staticmethod
    def canonicalize_path(path):
        parts = [x for x in path.split('/') if x != '']
        return '/'+'/'.join(parts)

    
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
        vmware = [ vim.AuthorizationManager.Permission(
            entity = self.mob,
            principal = permission.principal.principal,
            group = permission.principal.group,
            roleId = permission.role.mob.roleId,
            propagate = permission.propagate
        )
        for permission in permissions ]
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
        if not self.writable: return
        fields = self.injector.get_instance(custom_fields_key)
        for name, val_func in fields.items():
            field = self._ensure_custom_field(name, vim.ManagedEntity)
            timestamp = datetime.datetime.utcnow().replace(tzinfo=datetime.timezone.utc).isoformat()
            self.set_custom_field( field, value_func(self))



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
            return cfm.AddFieldDefinition(name=fname, moType=ftype)

    def set_custom_field(self,  field, value):
        entity = self.mob
        if isinstance(field, str):
            field = self._fetch_custom_field(field)
        content = self.connection.content
        cfm  = content.customFieldsManager
        cfm.SetField(entity=entity, key=field.key, value=value)

    def get_field_value(self,  field):
        '''Return the vmware custom field value or None if not set
        '''
        if isinstance(field, str):
            field = self._ensure_custom_field(field, vim.ManagedEntity)
            
        for val in self.mob.customValue:
            if (val.key == field.key) and (val.value != ''):
                return val
        return None

    def objects_with_field(self, field):
        # xxx this is broken
        # It's an instance method, but doesn't operate on the instance
        # It does an unconstrained search
        # The api has changed out from under it; I didn't fix because of the more serious problems
        # It's not really clear what this should be doing; why would we return a vmware ManagedEntity not a carthage object
        # Clarify after we understand what this is for.
        raise NotImplementedError
        content = self.connection.content
        container = content.viewManager.CreateContainerView(content.rootFolder, [vim.ManagedEntity], True)
        ret = set()
        for obj in container.view:
            try:
                if self._has_field(obj, field):
                    ret.add(obj)
            except vmodl.fault.ManagedObjectNotFound:
                pass
        container.Destroy()
        return ret

@inject(**VmwareManagedObject.injects)
class VmwareNamedObject(VmwareManagedObject):

    def __init__(self, name=None, *args, **kwargs):
        parent = kwargs.get('parent', None)
        if parent and '/' in name:
            raise TypeError("Cannot specify both a parent and a name containing a full path")
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

@inject(**VmwareNamedObject.injects)
class VmwareSpecifiedObject(VmwareNamedObject):

    def __init__(self, *args, spec=None, **kwargs):
        self.spec = spec
        super().__init__(*args, **kwargs)

def all_objs(content, root, objtype):
    vm = content.viewManager
    container = vm.CreateContainerView(root, objtype, True)
    for ref in container.view:
        yield ref
    container.Destroy()
