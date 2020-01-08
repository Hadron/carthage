# Copyright (C) 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .schema import ConfigSchema
from .types import ConfigPath

class BaseSchema(ConfigSchema, prefix = ""):
    base_dir: ConfigPath = "/srv/images/test"
    image_dir:ConfigPath = "{base_dir}"
    vm_image_dir: ConfigPath = "{base_dir}/vm"
    state_dir: ConfigPath = "{base_dir}/state"
    vm_image_size:int = 20000000000 #: default size of VM disks in Mb
    base_container_image:str = "/usr/share/hadron-installer/hadron-container-image.tar.gz"
    base_vm_image:str = "/usr/share/hadron-installer/direct-install-efi.raw.gz"
    container_prefix:str = 'carthage-'
    min_port:int = 9000 #: Minimum port for displays and databases
    num_ports: int = 500

    #: Path to  a checkout of hadron_operations
    hadron_operations: ConfigPath
    hadron_release: str = "unstable"

    #: If set, then when database.hadronindustries.com is generated, force every slot in the database to this value
    force_hadron_release: str = None
    delete_volumes: bool = False

    external_vlan_id: int = 0
    vlan_min:int = 1
    vlan_max:int = 4094

class TasksConfig(ConfigSchema, prefix = "tasks"):

    #: If True, then do not actually execute tasks
    dry_run: bool = False


