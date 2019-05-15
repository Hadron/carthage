import asyncio
import carthage. dependency_injection, carthage.config
import carthage.config
from .utils import memoproperty, when_needed
from .setup_tasks import *
from .dependency_injection import *
from .config import ConfigLayout, config_key, config_defaults
from .network import Network, NetworkConfig
from .machine import Machine
from .files import rsync_git_tree

import carthage.hadron_layout
import carthage.hadron
import carthage.container
import carthage.ssh
import carthage.pki
import carthage.vm

base_injector = carthage.dependency_injection.Injector()
carthage.config.inject_config(base_injector)
base_injector.add_provider(ssh.SshKey)
base_injector.add_provider(ssh.AuthorizedKeysFile)
base_injector.add_provider(carthage.network.Network)
base_injector.add_provider(asyncio.get_event_loop(), close = False)
base_injector.add_provider(carthage.hadron_layout.fake_internet)
base_injector.add_provider(carthage.network.external_network)
base_injector.add_provider(carthage.network.BridgeNetwork, allow_multiple = True)
base_injector.add_provider(carthage.hadron_layout.database_key, carthage.hadron_layout.test_database_container)
base_injector.add_provider(carthage.container.ssh_origin, carthage.hadron_layout.test_database_container)

base_injector.add_provider(carthage.container.container_image, carthage.hadron.hadron_container_image)
base_injector.add_provider(carthage.vm.vm_image, carthage.hadron.hadron_vm_image)
base_injector.add_provider(InjectionKey(carthage.ssh.SshAgent), carthage.ssh.ssh_agent)
base_injector.add_provider(carthage.pki.PkiManager)
