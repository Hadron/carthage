# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, logging, time
from dataclasses import dataclass
import carthage.ansible, carthage.network
import os.path
from ..machine import Machine
from ..dependency_injection import *
from ..config import ConfigLayout, config_defaults, config_key
from ..setup_tasks import SetupTaskMixin, setup_task, SkipSetupTask
from ..utils import memoproperty
from .image import VmwareDataStore, VmdkTemplate
from .inventory import VmwareStampable
from .connection import VmwareConnection
from .folder import VmwareFolder
from . import network

logger = logging.getLogger('carthage.vmware')

class VmwarePrincipal(object):
    def __init__(self, principal):
        self.principal = principal
    
class VmwareUser(VmwarePrincipal):
    @property
    def group(self): return False

class VmwareGroup(VmwarePrincipal):
    @property
    def group(self): return True

@inject(**VmwareStampable.injects)
class VmwareAuthorizationRole(VmwareStampable, kind='authorization_role'):

    def __init__(self, name, privIds=None, *args, **kwargs):
        self.name = name
        self.privIds = privIds
        VmwareStampable.__init__(self, *args, **kwargs)
        
    @memoproperty
    def stamp_descriptor(self):
        return self.name

    def _find_role(self):
        am = self.connection.content.authorizationManager
        for role in am.roleList:
            if role.name == self.name:
                self.mob = role
                return role

    @setup_task("create role")
    async def create(self):
        am = self.connection.content.authorizationManager
        role = self._find_role()
        if role is None:
            if self.privIds is None:
                raise KeyError
            am.AddAuthorizationRole(name=self.name, privIds=self.privIds)
        else:
            if self.privIds is not None:
                am.UpdateAuthorizationRole(roleId=role.roleId, newName=self.name, privIds=self.privIds)

    @create.invalidator()
    async def create(self):

        def lists_equal(a, b):
            return list_is_subset(a, b) and list_is_subset(b, a)

        def list_is_subset(a, b):
            for x in a:
                if x not in b:
                    print(x)
                    return False
            return True

        role = self._find_role()
        if role is not None and self.privIds is not None:
            if not lists_equal(role.privilege, self.privIds):
                return None
        return role

@dataclass
class VmwarePermission():
    principal : VmwarePrincipal
    role : VmwareAuthorizationRole
    propagate : bool
