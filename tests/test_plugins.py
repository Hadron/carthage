import pytest
from carthage.pytest import *
from carthage.dependency_injection import *
from carthage.plugins import load_plugin
from pathlib import Path


dir = Path(__file__).parent

@pytest.fixture()
def injector(ainjector):
    return ainjector.injector


def test_load_vmware(injector):
    injector(load_plugin, "carthage.vmware")
    
def test_dir_plugin(injector):
    injector(load_plugin, dir/"test_plugin")
