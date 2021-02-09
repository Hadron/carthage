# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import pytest
from carthage.pytest import *
from carthage.dependency_injection import *
from carthage import base_injector
from carthage.modeling.base import InjectableModel
from carthage.network import NetworkConfig

@pytest.fixture()
def injector():
    injector = base_injector(Injector)
    yield injector
    injector.close()
    
def test_modeling_class_injection(injector):
        class Layout(InjectableModel):
            class nc(NetworkConfig): pass
        model = injector(Layout)
        breakpoint()
        
            
