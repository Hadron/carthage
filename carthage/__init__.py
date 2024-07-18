# Copyright (C) 2018, 2019, 2020, 2021, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import atexit
import sys

import carthage.config
import carthage.dependency_injection

__all__ = []

from .utils import memoproperty, when_needed, relative_path
__all__ += ['memoproperty', 'when_needed', 'relative_path']

from .setup_tasks import *
__all__ += carthage.setup_tasks.__all__

from .dependency_injection import *
__all__ += carthage.dependency_injection.__all__

from .config import ConfigLayout, config_key, ConfigSchema
__all__ += ['ConfigLayout', 'config_key', 'ConfigSchema']


from .kvstore import KvStore, KvConsistency,AssignmentsExhausted, persistent_seed_path

__all__ += ['persistent_seed_path']


from . import deployment
from .deployment import  (
    find_deployables, DeployableFinder,
    find_orphan_deployables,
    run_deployment, run_deployment_destroy, find_deployable, destroy_policy, DeletionPolicy)
__all__ += ['find_deployable', 'find_deployables', 'DeployableFinder',
            'find_orphan_deployables',
            'run_deployment', 'run_deployment_destroy',
            'destroy_policy', 'DeletionPolicy']


from .network import Network, NetworkConfig, MacStore, V4Config, V4Pool

__all__ += ['Network', 'NetworkConfig', 'MacStore', 'V4Config', 'V4Pool']



from .machine import ssh_jump_host, Machine, AbstractMachineModel, MachineCustomization, ContainerCustomization, FilesystemCustomization, customization_task, BareMetalMachine
import carthage.ssh  # ssh import must come after machine
from .ssh import RsyncPath
import carthage.pki
from . import ansible
from . import cloud_init
from .files import rsync_git_tree, git_tree_hash


__all__ += ['ssh_jump_host', 'Machine', 'rsync_git_tree',
            'git_tree_hash',
            'RsyncPath',
            'AbstractMachineModel', 'MachineCustomization',
            'ContainerCustomization', 'FilesystemCustomization',
            'customization_task',
            'BareMetalMachine']

from . import image
from .image import ContainerVolume, wrap_container_customization

__all__ += ['ContainerVolume', 'wrap_container_customization']


from .system_dependency import MachineDependency, SystemDependency, disable_system_dependency

__all__ += ['MachineDependency', 'SystemDependency', 'disable_system_dependency']

import carthage.container
import carthage.local
from .local import LocalMachine, LocalMachineMixin
__all__ += ['LocalMachine', 'LocalMachineMixin']

import carthage.pki

import carthage.vm

import carthage.debian
from .debian import DebianContainerImage

__all__ += ['DebianContainerImage']

from .dns import DnsZone, PublicDnsManagement

__all__ += ['DnsZone', 'PublicDnsManagement']

from .plugins import CarthagePlugin
from . import plugins

__all__ += ['CarthagePlugin']

base_injector = carthage.dependency_injection.Injector()
base_injector.claim("base injector")
carthage.config.inject_config(base_injector)
base_injector.add_provider(plugins.PluginMappings)
base_injector.add_provider(deployment.MachineDeployableFinder)
base_injector.add_provider(ssh.SshKey)
base_injector.add_provider(ssh.AuthorizedKeysFile)
base_injector.add_provider(asyncio.get_event_loop(), close=False)
base_injector.add_provider(KvStore)
base_injector.add_provider(MacStore)
base_injector.add_provider(ansible.AnsibleConfig)
base_injector.add_provider(carthage.network.external_network)
base_injector.add_provider(carthage.network.BridgeNetwork, allow_multiple=True)

base_injector.add_provider(InjectionKey(carthage.ssh.SshAgent), carthage.ssh.ssh_agent)
base_injector.add_provider(carthage.pki.PkiManager)
base_injector(carthage.cloud_init.enable_cloud_init_plugins)

__all__ += ['base_injector']

base_injector(plugins.load_plugin_from_package, sys.modules[__name__])

# Things that need to import after base_injector is defined
from . import deployment_commands
base_injector(deployment_commands.register)

@atexit.register
def __done():
    asyncio.run(dependency_injection.shutdown_injector(base_injector))
    import sys
    sys.last_traceback = None
    sys.last_value = None
    import gc
    gc.collect()
