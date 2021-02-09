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
        
            
