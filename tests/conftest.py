# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

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
    
