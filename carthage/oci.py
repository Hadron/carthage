# Copyright (C)  2022, 2024, 2025, Hadron Industries, Inc.
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
from .config.types import ConfigPath
import carthage.machine
import carthage.network


__all__ = []

#: This key provides a string whose result is the container image name to pull  or an image ID that is locally available.  Examples include ``docker.io/library/debian:latest`` or ``debian:unstable``
oci_container_image = InjectionKey('oci/container_image')

__all__ += ['oci_container_image']

#: If provided this NetworkConfig will be used instead of
#InjectionKey(NetworkConfig) within containers.  That typically means
#that after resolve_networking, the container's injector will provide
#InjectionKey(NetworkConfig) with whatever was provided by this key.  Note that MachineModel does not respect this key.  So it will be respected when containers are used directly or for pods and pod models.
oci_container_network_config = InjectionKey(carthage.network.NetworkConfig, role='container')

__all__ += ['oci_container_network_config']


class OciManaged(SetupTaskMixin, AsyncInjectable):

    oci_labels: dict[str,str]

    def __init__(self, *, readonly=None, **kwargs):
        if readonly is not None:
            self.readonly = readonly
        super().__init__(**kwargs)
        self.oci_labels = {}
        
    @property
    def deployable_names(self):
        '''Uses each of self.deployable_name_prefixes as a name_type for self.name and self.id.
        '''
        res = []
        if getattr(self, 'name', None):
            for name_type in self.deployable_name_prefixes:
                res.append(f'{name_type}:{self.name}')
        if getattr(self, 'id', None):
            for name_type in self.deployable_name_prefixes:
                res.append(f'{name_type}Id:{self.id}')
        return res

    @property
    def deployable_name_prefixes(self):
        return [self.__class__.__name__]

    def __str__(self):
        try:
            return f'{self.deployable_name_prefixes[0]}:{self.name}'
        except Exception: return super().__str__()
        
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
        if self._find_result:
            return  # find was successful
        del self._find_result
        if self.readonly:
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

    async def dynamic_dependencies(self):
        return []

    def __repr__(self):
        res = f'<{self.__class__.__name__} '
        try: res += f'name:{self.name} '
        except Exception: pass
        try:
            if self.id:
                res += f'id: {self.id} '
        except Exception: pass
        return res+'>'
    

__all__ += ['OciManaged']


@dataclasses.dataclass
class OciExposedPort(Injectable):

    container_port: int
    host_ip: str = "0.0.0.0"
    host_port: int = ""
    proto: str = 'tcp'

    def default_instance_injection_key(self):
        if self.proto != 'tcp':
            return InjectionKey(OciExposedPort, container_port=self.container_port, proto=self.proto)
        return InjectionKey(OciExposedPort, container_port=self.container_port)


__all__ += ['OciExposedPort']


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

    @memoproperty
    def oci_command(self):
        '''Override the container command if non-None.
        Defaults to checking on the model
'''
        if hasattr(self.model, 'oci_command'):
            return self.model.oci_command
        return None


__all__ += ['OciContainer']


class OciImage(OciManaged):

    def __init__(self, *, oci_image_tag=None, id=None, **kwargs):
        if oci_image_tag:
            self.oci_image_tag = oci_image_tag
        if id:
            self.id = id
            self.readonly = True
        if not hasattr(self, 'oci_image_tag') and not hasattr(self, 'id'):
            raise TypeError('Either oci_image_tag or id is required')
        super().__init__(**kwargs)

    oci_image_author = ""
    oci_image_command = None
    oci_image_entry_point = None
    id = None

    @property
    def deployable_names(self):
        return ['image:'+self.oci_image_tag]

    def __init_subclass__(cls, no_auto_inject:bool=False, **kwargs):
        if no_auto_inject:
            oci_image_no_auto_inject.add(cls)
        from .modeling.decorators import propagate_key
        super().__init_subclass__(**kwargs)
        if getattr(cls, 'oci_image_tag', None):
            propagate_key(InjectionKey(OciImage, oci_image_tag=cls.oci_image_tag, _globally_unique=True), cls)

    @classmethod
    def default_class_injection_key(cls):
        return InjectionKey(OciImage, oci_image_tag=cls.oci_image_tag)

    @classmethod
    def supplementary_injection_keys(cls, k):
        yield InjectionKey(OciImage, oci_image_tag=cls.oci_image_tag)
        for k_new in super().supplementary_injection_keys(k):
            if (not issubclass(k_new.target, OciImage))  or k_new.target in oci_image_no_auto_inject:
                continue
            yield k_new

    async def filesystem_access(self):
        raise NotImplementedError
        yield ""

__all__ += ['OciImage']

#: A set of types that should not automatically be registered to provide images as supplementary injection keys
oci_image_no_auto_inject: set[type] = {OciImage}

@dataclasses.dataclass
class OciMount(Injectable):

    '''
    Represents a mount for a container.

    * If *source* is a string, config and environment variables will be substituted in it.

    * If *source* is an InjectionKey, it will be instantiated.

    * Container implementations may permit a *source* to be a managed object.  For example :class:`carthage.podman.PodmanContainer` permits *source* to be a :class:`carthage.podman.PodmanVolume`.
    '''
    
    destination: str
    source: str = None
    options: str = ""
    mount_type: str = 'volume'

    def default_instance_injection_key(self):
        return InjectionKey(OciMount, destination=self.destination)

    @classmethod
    def default_class_injection_key(self):
        return InjectionKey(OciMount, destination=self.destination)

    async def source_resolve(self, ainjector):
        if self.source is None: return None
        if isinstance(self.source, str):
            return ainjector.injector(ConfigPath, self.source)
        elif isinstance(self.source, InjectionKey):
            return await ainjector.get_instance_async(self.source)
        elif callable(self.source):
            return await ainjector(self.source)
        else:
            return self.source


__all__ += ['OciMount']


class OciPod(OciManaged):

    #: The name of the pod
    name: str = None
    #: the ID of the pod
    id: str = None

    def __init__(self, name=None, id=None, **kwargs):
        if name:
            self.name = name
        if id:
            self.id = id
        if not (self.name or self.id):
            raise TypeError('Either name or id is mandatory')
        super().__init__(**kwargs)
        if self.id:
            self.readonly = True

    @memoproperty
    def exposed_ports(self):
        '''Return a sequence of :class:`OciExposedPort` for any container ports that should be exposed.

        By default, instantiate all *OciExposedPort* instances in the injector.
        '''
        result = self.injector.filter_instantiate(OciExposedPort, ['container_port'])
        return [i[1] for i in result]


__all__ += ['OciPod']


@dataclasses.dataclass
class OciEnviron(Injectable):

    assignment: str
    scope: str = 'all'  # : or exec or image or container

    def default_instance_injection_key(self):
        if self.scope == 'all':
            return InjectionKey(OciEnviron, name=self.name)
        else:
            return InjectionKey(OciEnviron, name=self.name, scope=self.scope)

    @property
    def name(self):
        name, sep, value = self.assignment.partition('=')
        return name


__all__ += ['OciEnviron']


def host_mount(dir, readonly=False):
    options = []
    if readonly:
        options.append('ro=true')
    return OciMount(dir, dir, mount_type='bind', options=','.join(options))


__all__ += ['host_mount']

container_host_model_key = InjectionKey(carthage.machine.AbstractMachineModel, role='container_host')

__all__ += ['container_host_model_key']

class OciCredentials(Injectable):

    registry: str= None
    username: str = None
    password: str = None

    def __init__(self, *, registry=None, username=None, password=None, **kwargs):
        super().__init__(**kwargs)
        if registry:
            self.registry = registry
        if username:
            self.username = username
        if password:
            self.password = password

    @classmethod
    def default_class_injection_key(cls):
        if cls.registry:
            return InjectionKey(OciCredentials, registry=cls.registry)
        else:
            return super().default_class_injection_key(cls)

    def default_instance_injection_key(self):
        return InjectionKey(OciCredentials, registry=self.registry)

__all__ += ['OciCredentials']
