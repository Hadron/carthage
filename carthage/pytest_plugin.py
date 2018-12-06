# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import argparse, asyncio, json, pytest
from. import base_injector
from .dependency_injection import AsyncInjector


@pytest.fixture(scope = 'session')
def loop():
    ''':returns: Asyncio event loop
'''
    return asyncio.get_event_loop()

def pytest_collection_modifyitems(items):
    # This hook modifies items wrapped by @async_test to add fixtures used by the wrapped function
    # See the comment in that code for details
    
    for i in items:
        if isinstance(i,pytest.Function):
            if hasattr(i.function, '__signature__'):
                del i.keywords._markers['place_as']
                del i.keywords._markers['usefixtures']
                del i.keywords._markers['__signature__']
                i._fixtureinfo.argnames = tuple(i.function.__signature__.parameters.keys())

@pytest.fixture()
def ainjector():
    ainjector = base_injector(AsyncInjector)
    yield ainjector
    ainjector.close()

    
def pytest_addoption(parser):
    group = parser.getgroup("Carthage", "Carthage Continuous Integration Options")
    group.addoption('--carthage-config',
                    type = argparse.FileType('rt'),
                    help = "Specify yaml carthage config",
                    metavar = "file")
    group.addoption('--carthage-json',
                    metavar = "file",
                    type = argparse.FileType('wt'),
                    help = "Write json results to this file")
    
    

def pytest_configure(config):
    global json_out
    global json_log
    json_log = config.getoption('carthage_json')
    json_out = []
    
def pytest_runtest_logreport(report):
    if json_log is None: return
    d = {}
    for k in ('nodeid', 'location', 'keywords', 'outcome', 'longrepr', 'when', 'sections', 'duration'):
        d[k] = getattr(report, k)
    d['longrepr'] = report.longreprtext
    json_out.append(d)

def pytest_sessionfinish():
    global json_out, json_log
    if json_log is None: return
    json_log.write(json.dumps(json_out))
    json_out = []
    json_log.close()
    json_log = None
    
