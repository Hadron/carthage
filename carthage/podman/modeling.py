# Copyright (C)  2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.modeling import *
import carthage.modeling.implementation
from .base import *
from carthage import *
from carthage.dependency_injection import *

__all__ = []

class PodmanPodModel(PodmanPod, InjectableModel, metaclass=carthage.modeling.implementation.ModelingContainer):

    '''A container that can group a number of :class:`MachineModels` representing Podman containers.

    * By default, ``machine_implementation_key`` within a :class:`PodmanPodModel` is :class:`PodmanContainer`.

    * By default, when added to an enclosing injector, this model does not provide :class:`PodmanPod` in that context, although it does provide :class:`PodmanPod` within its own injector.  An example illlustrates::

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

    def __init__(self, **kwargs):
        if ('id' not in kwargs) and ('name' not in kwargs):
            if not self.id and not self.name:
                self.name = self.__class__.__name__
        super().__init__(**kwargs)

    pod_name_global = False #: If True, the pod name is globally unique

    @classmethod
    def our_key(self):
        return InjectionKey(self.__class__, name=self.name)

    @classmethod
    def supplementary_injection_keys(self, k):
        if k.constraints:
            yield InjectionKey(PodmanPod, **k.constraints)
            yield InjectionKey(PodmanPodModel, **k.constraints)
    
__all__ += ['PodmanPodModel']

class PodmanImageModel(PodmanImage, InjectableModel): pass

__all__ += ['PodmanImageModel']
