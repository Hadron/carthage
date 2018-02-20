import asyncio
import carthage. dependency_injection
import carthage.config
from .dependency_injection import AsyncInjector, Injector
from .config import ConfigLayout
import carthage.hadron_layout

base_injector = carthage.dependency_injection.Injector()
base_injector.add_provider(carthage.config.ConfigLayout)
base_injector.add_provider(ssh.SshKey)
base_injector.add_provider(ssh.AuthorizedKeysFile)
base_injector.add_provider(asyncio.get_event_loop())
base_injector.add_provider(carthage.hadron_layout.fake_internet)
base_injector.add_provider(carthage.hadron_layout.external_network)
base_injector.add_provider(carthage.hadron_layout.database_key, carthage.hadron_layout.test_database_container)

