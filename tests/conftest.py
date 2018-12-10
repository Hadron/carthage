import asyncio, pytest
from carthage.image import image_factory
from carthage import base_injector, ConfigLayout
from carthage.dependency_injection import AsyncInjector
from carthage.image import ContainerImage
from carthage.network import Network
from carthage.container import Container, container_image
import os.path, pytest, posix

@pytest.fixture(scope = 'session')
@async_test
async def test_injector(loop):
    if posix.geteuid() != 0:
        pytest.skip("Not running as root; volume tests skipped", )
    ainjector = base_injector(AsyncInjector)
    config = await ainjector(ConfigLayout)
    config.delete_volumes = True
    vol = await ainjector(ContainerImage, name = "base")
    base_injector.add_provider(container_image, vol)
    base_injector.add_provider(await ainjector(Network,'brint', delete_bridge = False))
    return ainjector

pytest_plugins = ('carthage.pytest_plugin',)




@pytest.fixture(scope = 'session')
def vm_image( loop, test_ainjector):
    ainjector = test_ainjector
    image = loop.run_until_complete(ainjector(image_factory, name = "base"))
    yield image
    image.close()
    

                

        
            
