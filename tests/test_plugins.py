# Copyright (C) 2021, 2024, 2026, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import pytest
import yaml
from carthage.pytest import load_test_plugin, async_test
from carthage.dependency_injection import *
from carthage.plugins import (load_plugin, PluginMappings,
                              CarthagePlugin, _parse_plugin_spec)
from pathlib import Path


dir = Path(__file__).parent


@pytest.fixture()
def injector(ainjector):
    return ainjector.injector


def test_load_podman(injector):
    plugin = injector(load_plugin, "carthage.podman")
    assert isinstance(plugin, CarthagePlugin)
    assert plugin.name == 'carthage.podman'
    assert plugin.package is not None
    # Duplicate loads return the same plugin object
    assert injector(load_plugin, "carthage.podman") is plugin


def test_dir_plugin(injector):
    plugin = injector(load_plugin, dir / "test_plugin")
    assert isinstance(plugin, CarthagePlugin)
    assert plugin.name == 'test_plugin'
    assert plugin.contains_resource('carthage_plugin.yml')
    # The plugin declares no package, so the loader exposes the root
    # python module through the package attribute.
    assert plugin.package is not None
    # Duplicate loads return the same plugin object
    assert injector(load_plugin, dir / "test_plugin") is plugin


def test_load_relative_to(injector):
    plugin = injector(load_plugin, './test_plugin', relative_to=dir)
    assert isinstance(plugin, CarthagePlugin)
    assert plugin.name == 'test_plugin'
    assert injector(load_plugin, dir / 'test_plugin') is plugin


def test_plugin_import_module(injector):
    plugin = injector(load_plugin, dir / 'test_plugin')
    root = plugin.package
    assert root.__name__ == 'carthage.carthage_plugins.test_plugin'
    submodule = plugin.import_module('submodule')
    assert submodule.from_submodule() == 'submodule-value'
    # Leading dots are tolerated
    assert plugin.import_module('./submodule') is submodule
    with pytest.raises(ImportError):
        plugin.import_module('no_such_module')
    # A plugin with no python package cannot import modules
    bare = CarthagePlugin(name='no-python', package=None,
                          metadata={'resource_dir': str(dir / 'test_plugin')})
    with pytest.raises(ImportError):
        bare.import_module('anything')


def test_load_test_plugin(injector):
    plugin = load_test_plugin(__file__, './test_plugin', injector=injector)
    assert isinstance(plugin, CarthagePlugin)
    assert plugin.name == 'test_plugin'
    assert plugin.import_module('submodule').VALUE == 'submodule-value'
    # Path objects also work and are resolved relative to the test file
    assert load_test_plugin(__file__, Path('test_plugin'), injector=injector) is plugin


@pytest.fixture()
def number():
    return 42


@pytest.fixture()
@async_test
async def async_fixture(number):
    return number


@async_test
async def test_async_test_fixtures(async_fixture):
    assert async_fixture == 42


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
        
