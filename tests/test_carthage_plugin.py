# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

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
    
