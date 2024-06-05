# Copyright (C) 2019, 2020, 2021, 2022, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .schema import ConfigSchema
from .types import ConfigPath, ConfigString


class BaseSchema(ConfigSchema, prefix=""):

    #: Name of layout to instantiate by default
    layout_name: str
    #: A path containing authorized keys to use in images.  If the
    # path starts with a | symbol, then the output of the given command
    # will be used.
    authorized_keys: ConfigPath = "|{hadron_operations}/hadron/inventory/config/default_keys.py"

    #: What resolv option to use when host networking is used with a container.  Likely options include bind-host, bind-stub or bind-uplink
    host_networking_resolv_conf: str = "bind-host"

    #: Ssh options that are always included
    global_ssh_options: str = '-oStrictHostKeyChecking=no'

    base_dir: ConfigPath = "~/.carthage"
    checkout_dir: ConfigPath = "{base_dir}/checkout"
    output_dir: ConfigPath = "{base_dir}/output"
    image_dir: ConfigPath = "{base_dir}"
    vm_image_dir: ConfigPath = "{base_dir}/vm"
    state_dir: ConfigPath = "{base_dir}/state"
    #: Directory for local ephemeral state like ssh_agent sockets
    local_run_dir: ConfigPath = "{state_dir}"
    vm_image_size: int = 20000000000  # : default size of VM disks in Mb
    base_container_image: str = "/usr/share/hadron-installer/hadron-container-image.tar.gz"
    base_vm_image: str = "/usr/share/hadron-installer/direct-install-efi.raw.gz"
    #: All containers and VMs are prefixed with this string in their machine names
    container_prefix: str = 'carthage-'
    min_port: int = 9000  # : Minimum port for displays and databases
    num_ports: int = 500

    #: If true, keep the ssh agent in the environment potentially
    # using production ssh keys rather than starting our own
    production_ssh_agent: bool = False
    #: Path to  a checkout of hadron_operations
    hadron_operations: ConfigPath
    delete_volumes: bool = False
    persist_local_networking: bool = False

    external_vlan_id: int = 0
    external_bridge_name: str = "brint"
    vlan_min: int = 1
    vlan_max: int = 4094


class TasksConfig(ConfigSchema, prefix="tasks"):

    #: If True, then do not actually execute tasks
    dry_run: bool = False


class DebianConfig(ConfigSchema, prefix="debian"):
    mirror: ConfigString = "http://deb.debian.org/debian"

    #: The mirror to use when running debootstrap. May be a file mirror for example to be used on the machine that will eventually be a mirror server.
    stage1_mirror: ConfigString = "{debian.mirror}"

    distribution: ConfigString = "bookworm"

    #: Whether to include security.debian.org and the updates source
    include_security: bool = True

    #: Any debootstrap option to include
    debootstrap_options: str = ""
