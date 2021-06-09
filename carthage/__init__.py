import asyncio
import atexit

import carthage.config
import carthage.dependency_injection

__all__ = []

from .utils import memoproperty, when_needed
__all__ += [ 'memoproperty', 'when_needed' ]

from .setup_tasks import *
__all__ += carthage.setup_tasks.__all__

from .dependency_injection import *
__all__ += carthage.dependency_injection.__all__

from .config import ConfigLayout, config_key, ConfigSchema
__all__ += [ 'ConfigLayout', 'config_key', 'ConfigSchema' ]
from .network import Network, NetworkConfig, MacStore, V4Config

__all__ += [ 'Network', 'NetworkConfig' , 'MacStore', 'V4Config']

from .machine import Machine, AbstractMachineModel, MachineCustomization, customization_task
import carthage.ssh # ssh import must come after machine
import carthage.pki
from . import ansible
from . import cloud_init

from .files import rsync_git_tree

__all__ += [ 'Machine',  'rsync_git_tree',
             'AbstractMachineModel', 'MachineCustomization', 'customization_task']

from .system_dependency import MachineDependency, SystemDependency, disable_system_dependency

__all__ += ['MachineDependency', 'SystemDependency', 'disable_system_dependency']

import carthage.container
import carthage.local
from .local import LocalMachine
__all__ += ['LocalMachine']

import carthage.pki

import carthage.vm

import carthage.debian
from .debian import DebianContainerImage

__all__ += ['DebianContainerImage']

base_injector = carthage.dependency_injection.Injector()
base_injector.claim("base injector")
carthage.config.inject_config(base_injector)
base_injector.add_provider(ssh.SshKey)
base_injector.add_provider(ssh.AuthorizedKeysFile)
base_injector.add_provider(asyncio.get_event_loop(), close = False)
base_injector.add_provider(MacStore)
base_injector.add_provider(ansible.AnsibleConfig)
base_injector.add_provider(carthage.network.external_network)
base_injector.add_provider(carthage.network.BridgeNetwork, allow_multiple = True)

base_injector.add_provider(InjectionKey(carthage.ssh.SshAgent), carthage.ssh.ssh_agent)
base_injector.add_provider(carthage.pki.PkiManager)
base_injector(carthage.cloud_init.enable_cloud_init_plugins)


__all__ += [ 'base_injector' ]

@atexit.register
def __done():
    asyncio.run(dependency_injection.shutdown_injector(base_injector))
    import sys
    sys.last_traceback = None
    sys.last_value = None
    import gc
    gc.collect()
    
    
