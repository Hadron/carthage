# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import argparse
import asyncio
import json
import logging
import pytest
import yaml
from dataclasses import replace, is_dataclass
from. import base_injector, ConfigLayout
from .dependency_injection import AsyncInjector


@pytest.fixture(scope='session')
def loop():
    ''':returns: Asyncio event loop
'''
    return asyncio.get_event_loop()


@pytest.fixture(scope='session')
def test_parameters(pytestconfig):
    try:
        return pytestconfig.carthage_test_parameters
    except AttributeError:
        pytest.skip("Test parameters not specified")


def pytest_collection_modifyitems(items):
    # This hook modifies items wrapped by @async_test to add fixtures used by the wrapped function
    # See the comment in that code for details

    for i in items:
        if isinstance(i, pytest.Function):
            if hasattr(i.function, '__signature__'):
                del i.keywords._markers['place_as']
                del i.keywords._markers['usefixtures']
                del i.keywords._markers['__signature__']
                if is_dataclass(i._fixtureinfo) and i._fixtureinfo.__dataclass_params__.frozen:
                    # _fixtureinfo is a frozen dataset. We can't change attrs of this. 
                    # use `replace` to generate a new _fixtureinfo object with the updated `argnames`
                    old = i._fixtureinfo
                    new = replace(old, argnames=tuple(i.function.__signature__.parameters.keys()))
                    i._fixtureinfo = new
                else:
                    i._fixtureinfo.argnames = tuple(i.function.__signature__.parameters.keys())



@pytest.fixture()
def ainjector():
    ainjector = base_injector.claim()(AsyncInjector)
    yield ainjector
    ainjector.close()


def pytest_addoption(parser):
    group = parser.getgroup("Carthage", "Carthage Continuous Integration Options")
    group.addoption('--carthage-config',
                    type=argparse.FileType('rt'),
                    default=[],
                    action='append',
                    help="Specify yaml carthage config; this configuration file describes where to put VMs and where to find hadron-operations.  It is not the test configuration for individual tests.  This option is typically used on the controller and not alongside --carthage-json on the system under test.",
                    metavar="file")
    group.addoption('--carthage-json',
                    metavar="file",
                    type=argparse.FileType('wt'),
                    help="Write json results to this file")
    group.addoption('--test-parameters', '--test-params',
                    metavar='file',
                    type=argparse.FileType('rt'),
                    help="YAML test parameters.  This option is typically used on the system under test alongside --carthage-json and not typically used with --carthage-config."
                    )
    group.addoption('--carthage-commands-verbose',
                    action='store_true',
                    help='Enable verbose logging of all carthage commands run')


def pytest_configure(config):
    global json_out
    global json_log
    json_log = config.getoption('carthage_json')
    json_out = []
    if not config.getoption('carthage_commands_verbose'):
        logging.getLogger('sh').setLevel(logging.ERROR)
        logging.getLogger('carthage.sh').propagate = False
        carthage_config = config.getoption('carthage_config')
    for c in carthage_config:
        config_layout = base_injector(ConfigLayout)
        try:
            config_layout.load_yaml(c)
        finally:
            c.close()
    test_params_yaml = config.getoption('test_parameters')
    if test_params_yaml:
        config.carthage_test_parameters = yaml.load(test_params_yaml)
        test_params_yaml.close()


def pytest_runtest_logreport(report):
    if json_log is None:
        return
    d = {}
    for k in ('nodeid', 'location', 'keywords', 'outcome', 'longrepr', 'when', 'sections', 'duration'):
        d[k] = getattr(report, k)
    d['longrepr'] = report.longreprtext
    json_out.append(d)


def pytest_sessionfinish():
    global json_out, json_log
    if json_log is None:
        return
    json_log.write(json.dumps(json_out))
    json_out = []
    json_log.close()
    json_log = None
