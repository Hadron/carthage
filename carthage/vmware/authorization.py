# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, logging, time
from dataclasses import dataclass
import os.path
from ..dependency_injection import *
from ..config import ConfigLayout, config_defaults, config_key
from ..setup_tasks import SetupTaskMixin, setup_task, SkipSetupTask
from ..utils import memoproperty
from .inventory import VmwareStampable, VmwareMarkable, VmwareConnection


logger = logging.getLogger('carthage.vmware')

@inject(config_layout = ConfigLayout,
        injector = Injector,
        connection = VmwareConnection)
class VmwareAuthorizationRole(VmwareStampable, VmwareMarkable):

    def __init__(self, name, privIds=[], *, config_layout, injector, connection):
        self.name = name
        self.privIds = privIds
        self.injector = injector.copy_if_owned().claim()
        self.config_layout = config_layout
        self.connection = connection
        self.ainjector = self.injector(AsyncInjector)
        self.inventory_object = None
        self.stamp_type = "authorization_role"
        VmwareStampable.__init__(self)
        
    @setup_task("create role")
    async def create_role(self):
        am = self.connection.content.authorizationManager
        found = None
        for role in am.roleList:
            if role.name == self.name:
                found = role
                break
        print(found)
        if found is None:
            am.AddAuthorizationRole(name=self.name, privIds=self.privIds)
        else:
            am.UpdateAuthorizationRole(roleId=found.roleId, newName=found.name, privIds=self.privIds)


class VmwarePrincipal(object):
    def __init__(self, principal):
        self.principal = principal
    
class VmwareUser(VmwarePrincipal):
    @property
    def group(self): return False

class VmwareGroup(VmwarePrincipal):
    @property
    def group(self): return True

@dataclass
class VmwarePermission():
    entity : VmwarePrincipal
    role : VmwareAuthorizationRole
    propagate : bool

@inject(config_layout = ConfigLayout,
        injector = Injector,
        connection = VmwareConnection)
class VmwareEntityPermission(VmwareStampable, VmwareMarkable):

    def __init__(self,
                 *, config_layout, injector, connection):
        self.entity = entity
        self.role = role
        self.propagate = propagate

        self.injector = injector.copy_if_owned().claim()
        self.config_layout = config_layout
        self.connection = connection
        self.ainjector = self.injector(AsyncInjector)
        self.inventory_object = None
        self.stamp_type = "entity_permission"
        VmwareStampable.__init__(self)
        
    @setup_task("create entity permission")
    async def create_permission(self):
        am = self.connection.content.authorizationManager
        found = None
        for role in am.roleList:
            if role.name == self.name:
                found = role
                break
        print(found)
        if found is None:
            am.AddAuthorizationRole(name=self.name, privIds=self.privIds)
        else:
            am.UpdateAuthorizationRole(roleId=found.roleId, newName=found.name, privIds=self.privIds)

