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
from carthage.modeling.base import *
from carthage.modeling.implementation import ModelingContainer
from carthage.modeling.decorators import *
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

        
            
def test_container(injector):
    class Layout(InjectableModel, metaclass = ModelingContainer):

        add_provider(InjectionKey("key1"),
                     42)
        access_key_1 = injector_access("key1")
        
        net_config = injector_access(InjectionKey(NetworkConfig, domain = "evil.com"))
        class RedEnclave(Enclave):

            domain = "evil.com"

            class nc(NetworkConfig): pass
    res = injector(Layout)
    nc = res.injector.get_instance(InjectionKey(
        NetworkConfig, domain = "evil.com"))
    assert nc is Layout.RedEnclave.nc
    assert res.net_config is res.RedEnclave.nc
    assert res.access_key_1 == 42
    
    
    
    
def test_dynamic_name(injector):
    class Layout(ModelGroup):

        for i in range(3):
            @dynamic_name(f'i{i+1}')
            class ignored(InjectableModel): square = i*i
    assert Layout.i3.square == 4
    breakpoint()
