# Copyright (C)  2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import dataclasses
from carthage.dependency_injection import *
from .setup_tasks import setup_task, SetupTaskMixin
from .utils import memoproperty


__all__ = []

#: This key provides a string whose result is the container image name to pull  or an image ID that is locally available.  Examples include ``docker.io/library/debian:latest`` or ``debian:unstable``
oci_container_image = InjectionKey('oci/container_image')

__all__ += ['oci_container_image']


@inject_autokwargs(
    oci_read_only=InjectionKey('oci_read_only', _optional=NotPresent)
    )
class OciManaged(SetupTaskMixin, AsyncInjectable):

    #:Should this object be treated as read only
    oci_read_only = False
    async def find(self):
        '''Returns falsy if the object does not exist.  Ideally returns the creation time in unix time, otherwise returns True if the creation time cannot be determined.
        '''
        raise NotImplementedError

    @setup_task("Construct Object", order=400)
    async def find_or_create(self):
    # Make sure we call find.  When called by setup_tasks,
    # check_completed runs first and so we do not need to call, but
    # for an explicit call we need to call find ourselves.
        if not hasattr(self, '_find_result'):
            self._find_result = await self.find()
        if self._find_result: return #find was successful
        del self._find_result
        if self.oci_read_only:
            raise RuntimeError(f'{self} is read only but does not exist')
        
        await self.do_create()
        self._find_result = await self.find()
        return self._find_result

    @find_or_create.check_completed()
    async def find_or_create(self):
        self._find_result = await self.find()
        return self._find_result

    async def do_create(self):
        raise NotImplementedError

__all__ += ['OciManaged']

@dataclasses.dataclass
class OciExposedPort(Injectable):

    container_port: int
    host_ip: str = "0.0.0.0"
    host_port: int = ""

    def default_instance_injection_key(self):
        return InjectionKey(OciExposedPort, container_port=self.container_port)

@inject_autokwargs(
    oci_interactive=InjectionKey('oci_interactive', _optional=NotPresent),
    oci_tty=InjectionKey('oci_tty', _optional=NotPresent),
        )
class OciContainer(OciManaged):

    #: Should stdin be kept open?
    oci_interactive = False

    #: Allocate a tty for stdio
    oci_tty = False

    @memoproperty
    def exposed_ports(self):
        '''Return a sequence of :class:`OciExposedPort` for any container ports that should be exposed.

        By default, instantiate all *OciExposedPort* instances in the injector.
        '''
        result = self.injector.filter_instantiate(OciExposedPort, ['container_port'])
        return [i[1] for i in result]

    @memoproperty
    def mounts(self):
        '''
        Sequence of :class:`OciMount` objects to be mounted to the container.

        By default instantiate *OciMount* objects in the injector hierarchy.
        '''
        results = self.injector.filter_instantiate(OciMount, ['destination'])
        return [i[1] for i in results]
    
    #: Override the entry point in the container if set
    oci_entry_point = None

    #: Override the default command if set
    oci_command = None
    
__all__ += ['OciContainer']


class OciImage(OciManaged):

    def __init__(self, *, oci_image_tag=None, id=None, **kwargs):
        if oci_image_tag: self.oci_image_tag = oci_image_tag
        if id: self.id = id
        if not hasattr(self, 'oci_image_tag') and not hasattr(self, 'id'):
            raise TypeError('Either oci_image_tag or id is required')
        super().__init__(**kwargs)

    oci_image_author = ""
    oci_image_cmd = None
    oci_image_entry_point = None
    id = None

__all__ += ['OciImage']

@dataclasses.dataclass
class OciMount(Injectable):


    destination: str
    source: str
    options: str = ""
    mount_type: str = 'volume'

    def default_instance_injection_key(self):
        return InjectionKey(OciMount, destination=self.destination)

    @classmethod
    def default_class_injection_key(self):
        return InjectionKey(OciMount, destination=self.destination)

__all__ += ['OciMount']

class OciPod(OciManaged):

    #: The name of the pod
    name:str = None
    #: the ID of the pod
    id:str  = None

    def __init__(self, name=None, id=None, **kwargs):
        if name: self.name  = name
        if id: self.id = id
        if not (self.name or self.id):
            raise TypeError('Either name or id is mandatory')
        super().__init__(**kwargs)
        if self.id: self.oci_read_only = True


    @memoproperty
    def exposed_ports(self):
        '''Return a sequence of :class:`OciExposedPort` for any container ports that should be exposed.

        By default, instantiate all *OciExposedPort* instances in the injector.
        '''
        result = self.injector.filter_instantiate(OciExposedPort, ['container_port'])
        return [i[1] for i in result]

__all__ += ['OciPod']
