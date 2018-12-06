# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, pytest
from. import base_injector
from .dependency_injection import AsyncInjector


@pytest.fixture(scope = 'session')
def loop():
    ''':returns: Asyncio event loop
'''
    return asyncio.get_event_loop()

def pytest_collection_modifyitems(items):
    # This hook modifies items wrapped by @async_test to add fixtures used by the wrapped function
    # See the comment in that code for details
    
    for i in items:
        if isinstance(i,pytest.Function):
            if hasattr(i.function, '__signature__'):
                i._fixtureinfo.argnames = tuple(i.function.__signature__.parameters.keys())

@pytest.fixture()
def ainjector():
    ainjector = base_injector(AsyncInjector)
    yield ainjector
    ainjector.close()

    
