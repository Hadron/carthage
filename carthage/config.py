# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .dependency_injection import inject, Injectable

class ConfigLayout(Injectable):

    image_dir = "/srv/images/test"
    base_container_image = "/usr/share/hadron-installer/hadron-container-image.tar.gz"
    container_prefix = 'carthage-'
    state_dir ="/srv/images/test/state"
    hadron_operations = "/home/hartmans/hadron-operations"
    delete_volumes = False
