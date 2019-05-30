import logging, time
from carthage.image import ContainerImage
from carthage.network import Network
from carthage.container import Container, container_image
from carthage.pytest import *
import os.path, pytest, posix
from carthage import base_injector, AsyncInjector



@pytest.fixture()
def container(test_ainjector, loop):
    ainjector = test_ainjector
    container = loop.run_until_complete(ainjector(Container, name = "container-1"))
    yield container
    if container.running:
        loop.run_until_complete(container.stop_container())

@async_test
async def test_start_container(container, loop):
    await container.start_container()

@async_test
async def test_container_running(container, loop):
    async with container.container_running:
        container.shell("/bin/ls")
        
