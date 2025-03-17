# Copyright (C)  2022, 2023, 2024, 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import carthage.config

__all__ = []

import carthage.deployment
from .base import *
__all__ += ['PodmanPod', 'PodmanContainer', 'PodmanImage',
            'podman_push_images',
            'PodmanFromScratchImage', 'podman_image_volume_key',
            'image_layer_task', 'ContainerfileImage', 'PodmanVolume']

from .modeling import PodmanPodModel, PodmanImageModel, ContainerfileImageModel

__all__ += ['PodmanPodModel', 'PodmanImageModel', 'ContainerfileImageModel']

from .container_host import(
    LocalPodmanContainerHost,
    RemotePodmanHost,
    podman_container_host,
    LocalPodmanSocket,
    podman_sftp_server_mount,
    )


__all__ += ['LocalPodmanContainerHost', 'RemotePodmanHost', 'podman_container_host',
            'LocalPodmanSocket', 'podman_sftp_server_mount',
            ]

class PodmanConfig(carthage.config.ConfigSchema, prefix='podman'):

    #: Set to never or missing to reduce network traffic
    pull_policy:str = 'newer'
    #: An image used to gain access to volumes. Must have /bin/sh.
    volume_access_image: str = 'ghcr.io/hadron/carthage_volume_access:latest'
    
    
class PodmanDeployableFinder(carthage.DeployableFinder):

    name = 'podman'

    async def find(self, ainjector):
        '''
        Find all PodmanPods, PodmanVolumes, and PodmanImages. PodmanContainers are found by the machine deployable finder.
        '''
        result = []
        for c in (PodmanPod, PodmanVolume):
            filter_result = await ainjector.filter_instantiate_async(
            c, ['name'],
                ready=False,
                stop_at=ainjector)
            result += [x[1] for x in filter_result]
        filter_result = await ainjector.filter_instantiate_async(
            PodmanImage, ['oci_image_tag'], ready=False)
        result += [x[1] for x in filter_result]
        return result
    

@carthage.inject(injector=carthage.Injector)
def carthage_plugin(injector):
    injector.add_provider(PodmanNetwork, allow_multiple=True)
    injector.add_provider(PodmanDeployableFinder)
    
