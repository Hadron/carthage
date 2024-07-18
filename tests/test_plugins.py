# Copyright (C) 2021, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import pytest
import yaml
from carthage.pytest import *
from carthage.dependency_injection import *
from carthage.plugins import load_plugin, PluginMappings, _parse_plugin_spec
from pathlib import Path


dir = Path(__file__).parent


@pytest.fixture()
def injector(ainjector):
    return ainjector.injector


def test_load_vmware(injector):
    injector(load_plugin, "carthage.vmware")


def test_dir_plugin(injector):
    injector(load_plugin, dir / "test_plugin")

def test_plugin_mappings(injector):
    test_data_path = dir/'test_plugin_mappings.yml'
    test_data = yaml.safe_load(test_data_path.read_text())
    for environment in test_data:
        plugin_mappings =  injector(PluginMappings)
        for mapping in environment['mappings']:
            plugin_mappings.add_mapping(mapping)
    for test in environment['tests']:
        spec = _parse_plugin_spec(test['spec'])
        mapped = plugin_mappings.map(spec)
        expected = _parse_plugin_spec(test['expected'])
        assert mapped == expected, f'Unexpected result mapping {spec}'
        
