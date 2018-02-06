import asyncio
import carthage. dependency_injection
import carthage.config

base_injector = carthage.dependency_injection.Injector()
base_injector.add_provider(carthage.config.ConfigLayout)
base_injector.add_provider(asyncio.get_event_loop())
