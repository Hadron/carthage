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

    
