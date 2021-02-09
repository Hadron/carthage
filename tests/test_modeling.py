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
        nc = model.injector.get_instance(NetworkConfig)
        assert nc is Layout.nc

def test_namespace_cascade(injector):
    class Layout(InjectableModel):
        green = "green"
        class inner(InjectableModel):
            assert green == "green"
            green = "blue"
        class inner2 (InjectableModel):
            inner = inner
            class MoreInner:
                #This should break the chain because it is not a InjectableModel
                class MostInner(InjectableModel):
                    assert 'inner' not in locals().keys()
                    
    res = injector(Layout.inner)
    assert res.green == "blue"
    assert Layout.inner2.inner  is Layout.inner

        
            
