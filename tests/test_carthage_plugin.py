import pytest
from carthage.pytest import *
from carthage  import base_injector, AsyncInjector, inject
from carthage import ConfigLayout

def test_test_parameters(test_parameters):
    return True

@async_test
async def test_async_test():
    return True

@async_test
async def test_async_test_with_loop(loop):
    return True



@async_test
@inject(config = ConfigLayout)
def test_carthage_injection(config, ainjector):
    assert config.delete_volumes == False
    
