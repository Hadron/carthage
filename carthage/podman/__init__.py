# Copyright (C)  2022, 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
__all__ = []

import carthage.deployment
from .base import *
__all__ += ['PodmanPod', 'PodmanContainer', 'PodmanImage',
            'PodmanFromScratchImage', 'podman_image_volume_key',
            'image_layer_task', 'ContainerfileImage', 'PodmanVolume']

from .modeling import PodmanPodModel, PodmanImageModel, ContainerfileImageModel

__all__ += ['PodmanPodModel', 'PodmanImageModel', 'ContainerfileImageModel']

from .container_host import LocalPodmanContainerHost, RemotePodmanHost, podman_container_host

__all__ += ['LocalPodmanContainerHost', 'RemotePodmanHost', 'podman_container_host']

class PodmanDeployableFinder(carthage.DeployableFinder):

    name = 'podman'

    async def find(self, ainjector):
        '''
        Find all PodmanPods. PodmanContainers are found by the machine deployable finder.
        '''
        result = []
        filter_result = await ainjector.filter_instantiate_async(
            PodmanPod, ['name'],
            ready=False,
            stop_at=ainjector)
        result += [x[1] for x in filter_result]
        return result
    

@carthage.inject(injector=carthage.Injector)
def carthage_plugin(injector):
    injector.add_provider(PodmanNetwork, allow_multiple=True)
    injector.add_provider(PodmanDeployableFinder)
    
