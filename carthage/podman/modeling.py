# Copyright (C)  2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import typing

from carthage.modeling import *
import carthage.modeling.implementation
from .base import *
from carthage import *
from carthage.dependency_injection import *
from carthage.oci import OciImage, oci_container_network_config

__all__ = []


class PodmanPodModel( carthage.machine.NetworkedModel, ModelContainer, AsyncInjectable):

    '''A container that can group a number of :class:`MachineModels` representing Podman containers.

    * By default, ``machine_implementation_key`` within a :class:`PodmanPodModel` is :class:`PodmanContainer`.

    * By default, when added to an enclosing injector, this model does not provide :class:`PodmanPod` in that context, although it does provide :class:`PodmanPod` within its own injector.  An example illustrates::

        class Layout(CarthageLayout):

            #Even though this layout includes  PodmanPods, PodmanPod is not provided in the injector of the layout.  That means containers outside of a PodmanPodModel will not be associated with a pod by default

            class pod1(PodmanPodModel):

                # These containers are within pod1 and because within its own scope pod1 provides PodmanPod, the containers will be added to the pod.

                class container1(MachineModel): pass

                class container2(MachineModel): pass

            # But this container is not in a pod.

            class container3(MachineModel): pass

    In contrast to using PodmanPodModel, :class:`PodmanPod` can be used along side containers in another modeling grouping.  When used this way :class:`PodmanPod` does provide :class:`PodmanPod` in the enclosing injector::

        class layout(CarthageLayout):

            class group1(ModelGroup):

                # Everything in this group is pod1

                class pod1(PodmanPod): name = 'pod1'

                class container1(MachineModel): pass

    '''

    add_provider(machine_implementation_key, dependency_quote(PodmanContainer))
    self_provider(carthage.machine.network_namespace_key)

    @classmethod
    def name_for(cls):
        return getattr(cls, 'name', cls.__name__)
    
    def __init__(self, **kwargs):
        self.name = self.name_for()
        super().__init__(**kwargs)
        self.network_links = {}
        pod_key = InjectionKey(PodmanPod, name=self.name, _globally_unique=self.pod_name_global)
        if pod_key in self.ignored_by_transclusion:
            self.injector.add_provider(InjectionKey(PodmanPod), injector_access(pod_key), close=False)
        else:
            class Pod(PodmanPod):
                name = self.name
            self.injector.add_provider(InjectionKey(PodmanPod), Pod)

    def __init_subclass__(cls, template=False, **kwargs):
        super().__init_subclass__(**kwargs)
        if not template:
            cls.add_provider(InjectionKey(PodmanPod, name=cls.name_for(), _globally_unique=cls.pod_name_global),
                         injector_access(InjectionKey(PodmanPod)),
                         close=False,
                         propagate=cls.pod_name_global,
                         transclusion_overrides=cls.pod_name_global)
            cls.add_provider(InjectionKey(carthage.machine.ResolvableModel, name=cls.name_for(), role='pod', _globally_unique=cls.pod_name_global),
                         injector_access(InjectionKey(PodmanPod)),
                         close=False,
                         propagate=cls.pod_name_global,
                         transclusion_overrides=cls.pod_name_global)
            propagate_key(InjectionKey(carthage.machine.ResolvableModel, name=cls.name_for()+'-pod', _globally_unique=True))(cls)
            propagate_key(cls.our_key(), cls)
                                         
                                          
    pod_name_global = True  # : If True, the pod name is globally unique

    @classmethod
    def our_key(self):
        name = self.name_for()
        return InjectionKey(self.__class__, name=name, _globally_unique=self.pod_name_global)

    @classmethod
    def supplementary_injection_keys(self, k):
        if k.constraints:
            yield InjectionKey(PodmanPodModel, **k.constraints)
            if 'name' in k.constraints and not issubclass(k.target, carthage.machine.ResolvableModel):
                yield InjectionKey(carthage.machine.ResolvableModel, name=k.constraints['name']+'-pod',
                                   _globally_unique=self.pod_name_global)

    pod = injector_access(InjectionKey(PodmanPod))

    def __repr__(self):
        return f'<{self.__class__.__name__} name:{self.name}>'

    async def resolve_networking(self, force:bool = False):
        '''Like
        :meth:`carthage.machine.NetworkedModel.resolve_networking`
        except that it looks for :data:`oci_container_network_config`.
        If that key is present, that network config is used instead
        of ``InjectionKey(NetworkConfig)``.  Doing so allows
        containers that are lexically contained in their host to have
        their own NetworkConfig.

        '''
        if not force and self.network_links:
            return
        container_config = await self.ainjector.get_instance_async(InjectionKey(oci_container_network_config, _optional=NotPresent))
        if container_config is not NotPresent:
            try:
                self.injector.add_provider(InjectionKey(NetworkConfig), dependency_quote(container_config))
            except ExistingProvider: pass
        await super().resolve_networking(force=force)
        for net in set(map( lambda l:l.net, self.network_links.values())):
            net.assign_addresses()
    
__all__ += ['PodmanPodModel']


class PodmanImageModel(ImageRole, PodmanImage):

    '''
    Like a :class:`PodmanImage` excetp:

    * This is an :class:`InjectableModel` so modeling language constructs can be used

    * Any :class:`FilesystemCustomization` or :class:`ContainerCustomization` that are registered with the injector are automatically treated as image layers **after** any explicit setup_tasks.

    '''

    # We need to have some of the attributes from AbstractMachineModel so that start_machine can work
    override_dependencies: typing.Union[bool, Injector, Injectable, InjectionKey] = False

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.injected_tasks_added = False
        self.injector.add_provider(InjectionKey(PodmanImageModel), self)
        self.network_links = {}

    def add_injected_tasks(self):
        for k, customization in self.injector.filter_instantiate(
                carthage.machine.BaseCustomization, [
                    'description'], stop_at=self.injector):
            if issubclass(customization, (
                    carthage.machine.ContainerCustomization,
                    carthage.machine.FilesystemCustomization,
                    )):
                self.add_setup_task(image_layer_task(customization))
            else:
                logger.warn(f'{customization} is an inappropriate customization for {self}')
        self.injected_tasks_added = True

    async def build_image(self):
        if not self.injected_tasks_added:
            self.add_injected_tasks()
        return await super().build_image()

    def __init_subclass__(cls, **kwargs):
        if issubclass(cls, MachineModel):
            raise TypeError(cls.__name__+' should not be both a PodmanImageModel and MachineModel.  This probably means you tried to add a role that is not an ImageRole to a PodmanImageModel')
        super().__init_subclass__(**kwargs)
        
        

__all__ += ['PodmanImageModel']

class ContainerfileImageModel(ContainerfileImage, InjectableModel):

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.oci_image_tag:
            provides(InjectionKey(OciImage, tag=cls.oci_image_tag))(cls)
                     

__all__ += ['ContainerfileImageModel']

