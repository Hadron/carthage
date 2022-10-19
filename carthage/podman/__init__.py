# Copyright (C)  2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
__all__ = []

import carthage
from .base import *
__all__ += ['PodmanPod', 'PodmanContainer', 'PodmanImage',
            'PodmanFromScratchImage', 'podman_image_volume_key',
            'image_layer_task']

from .modeling import PodmanPodModel

__all__ += ['PodmanPodModel']

@carthage.inject(injector=carthage.Injector)
def carthage_plugin(injector):
    pass
