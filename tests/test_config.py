import pytest
from carthage.config import *
from carthage.dependency_injection import Injector, inject

def test_add_config(ainjector):
    injector = ainjector.injector
    class Defaults(ConfigSchema, prefix = ""):
        foo:int = 33
    cl = injector(ConfigLayout)
    assert cl.foo == 33
    
def test_override_config(ainjector):
    injector = ainjector.injector

    @inject(cl = ConfigLayout)
    def fn(cl):
        assert cl.hadron_operations == 99
    injector.add_provider(config_key("hadron_operations"), 99)
    injector(fn)
                          
