import asyncio
import carthage. dependency_injection
import carthage.config
from .dependency_injection import AsyncInjector, Injector
from .config import ConfigLayout
from . import ssh

base_injector = carthage.dependency_injection.Injector()
base_injector.add_provider(carthage.config.ConfigLayout)
base_injector.add_provider(ssh.SshKey)
base_injector.add_provider(ssh.AuthorizedKeysFile)
base_injector.add_provider(asyncio.get_event_loop())

