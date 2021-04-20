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

from .network import Network, NetworkConfig
__all__ += [ 'Network', 'NetworkConfig' ]

from .machine import Machine, AbstractMachineModel, MachineCustomization, customization_task
import carthage.ssh # ssh import must come after machine
import carthage.pki


from .files import rsync_git_tree

__all__ += [ 'Machine',  'rsync_git_tree',
             'AbstractMachineModel', 'MachineCustomization', 'customization_task']

import carthage.container
import carthage.hadron
import carthage.hadron_layout
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
base_injector.add_provider(carthage.network.Network)
base_injector.add_provider(asyncio.get_event_loop(), close = False)
base_injector.add_provider(carthage.hadron_layout.fake_internet)
base_injector.add_provider(carthage.network.external_network)
base_injector.add_provider(carthage.hadron_layout.services_vlan_key, carthage.hadron_layout.services_vlan)
base_injector.add_provider(carthage.network.BridgeNetwork, allow_multiple = True)
base_injector.add_provider(carthage.hadron_layout.database_key, carthage.hadron_layout.test_database_container)
base_injector.add_provider(carthage.network.host_map_key, carthage.hadron_layout.hadron_host_map)
try:
    import carthage.hadron.database
    base_injector.add_provider(hadron.database.RemotePostgres)
except ImportError: pass
base_injector.add_provider(carthage.container.ssh_origin, carthage.hadron_layout.test_database_container)
base_injector.add_provider(carthage.machine.ssh_origin_vrf, "vrf-internet")
base_injector.add_provider(carthage.hadron.hadron_vault_key, carthage.hadron_layout.hadron_vault_container)

base_injector.add_provider(carthage.container.container_image, carthage.hadron.hadron_container_image)
base_injector.add_provider(carthage.vm.vm_image, carthage.hadron.hadron_vm_image)
base_injector.add_provider(InjectionKey(carthage.ssh.SshAgent), carthage.ssh.ssh_agent)
base_injector.add_provider(carthage.pki.PkiManager)

__all__ += [ 'base_injector' ]

@atexit.register
def __done():
    asyncio.run(dependency_injection.shutdown_injector(base_injector))
    import sys
    sys.last_traceback = None
    sys.last_value = None
    import gc
    gc.collect()
    
    
