import asyncio, pytest
from carthage.image import image_factory
from carthage import base_injector
from carthage.dependency_injection import AsyncInjector


@pytest.fixture(scope = 'session')
def loop():
    return asyncio.get_event_loop()



@pytest.fixture(scope = 'session')
def vm_image( loop):
    ainjector = base_injector(AsyncInjector)
    image = loop.run_until_complete(ainjector(image_factory, name = "base"))
    yield image
    image.close()
    
