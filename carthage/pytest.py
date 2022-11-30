# Copyright (C) 2018, 2019, 2021, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import functools
import inspect
import json
import pytest
import sys
import time

from .dependency_injection import inject, InjectionKey

from _pytest.reports import TestReport
from _pytest.nodes import Node


'''
Decorators and functions for use with Carthage.

This module is typically used alongside the :ref:`carthage.pytest_plugin` Pytest plugin.  This module contains decorators like :funcref:`async_test` and that plugin provides fixtures and hooks needed for these items to work.  Supported functionality includes:

* *asyncio* tests that support :ref:`carthage.inject` style dependency injection

* Running a subtest within an item implementing :ref:`SshMixin` and collecting the results.

'''


class TestTiming:

    time_remaining = 120
    previous_timing = None
    __test__ = False

    '''
    Usage::

         with TestTiming(300):
              # Block of code that is given 300 seconds to run

'''

    def __init__(self, timing: float):
        self.time_remaining = timing
        self.initial_time_remaining = timing

    def __enter__(self):
        global test_timing
        self.previous_timing = test_timing
        test_timing = self

    def __exit__(self, *args):
        global test_timing
        test_timing = self.previous_timing


test_timing = None


def time_loop(loop, future):
    '''Run *future* in 5 second slices, adjusting time_remaining.  If time runs out, cancel the future but eventually raise TimeoutError.
'''
    done = tuple()
    #: None or the eventual error message to print.
    timed_out = None
    time_delta = 5
    while not done:
        now = time.time()
        time_remaining = test_timing.time_remaining
        done, pending = loop.run_until_complete(asyncio.wait(
            [future], timeout=time_remaining if time_remaining < time_delta else time_delta))
        # test_timing.time_remaining may have increased while running.
        if not done:
            test_timing.time_remaining -= time.time() - now
            if test_timing.time_remaining < 0:
                if timed_out:
                    raise TimeoutError(timed_out)
                timed_out = f'{future} failed to complete in {test_timing.initial_time_remaining} seconds'
                future.cancel(msg='timed out')
                test_timing.time_remaining = 30
    if timed_out:
        if not future.cancelled():
            raise ValueError('Task failed to cancel on timeout')
        raise TimeoutError(timed_out)


def async_test(t):
    '''A decorator for wrapping a test. *t* is expected to be a coroutine
    and will be run inside an event loop.  The test may take either
    Carthage style dependencies using the :funcref:`carthage.inject`
    decorator or Pytest style fixtures as function arguments.  Any
    Carthage style injected item will be removed from the list of
    Pytest style injectors.

    '''
    # This is messy because Pytest's introspection logic does not
    # respect inspect.Signature.  So we don't actually include the
    # _wrapped_ item and explicitly mark tests with pytest.usefixture
    # to indicate which fixtures are required.  However, we need to
    # depend on a pytest_collect_modifyitems hook in
    # carthage.pytest_plugin to make the fixtures available to the
    # function.

    sig = inspect.signature(t)
    orig_loop = True
    orig_ainjector = True

    @functools.wraps(t)
    def wrapper(loop, *args, **kwargs):
        global test_timing
        test_timing = TestTiming(TestTiming.time_remaining)

        if orig_loop:
            kwargs['loop'] = loop
        ainjector = kwargs.get('ainjector', None)
        if ainjector is None:
            task = asyncio.ensure_future(t(*args, **kwargs), loop=loop)
        else:
            if not orig_ainjector:
                del kwargs['ainjector']
            task = asyncio.ensure_future(ainjector(t, *args, **kwargs), loop=loop)
        time_loop(loop, task)
        return task.result()
    params = list(sig.parameters.values())
    try:
        params = list(filter(lambda p: p.name not in t._injection_dependencies, params))
    except AttributeError:
        pass  # no @inject call
    param_names = set(p.name for p in params)
    if 'loop' not in param_names:
        orig_loop = False
        params.append(inspect.Parameter(name="loop", kind=inspect._KEYWORD_ONLY))
    if 'ainjector' not in param_names:
        orig_ainjector = False
        if hasattr(t, '_injection_dependencies'):
            params.append(inspect.Parameter(name="ainjector", kind=inspect._KEYWORD_ONLY))
    sig = sig.replace(parameters=params)
    wrapper.__signature__ = sig
    pytest.mark.usefixtures(*sig.parameters.keys())(wrapper)
    del wrapper.__dict__['__wrapped__']
    wrapper.place_as = t
    return wrapper


_test_results_serial = 0


async def subtest_controller(request, target, pytest_args,
                             python_path="", ssh_agent=False):
    '''Ssh into a given machine using a :class:`carthage.SshMixin` and
    run a series of pytests.  This is typically run by a :ref:`test
    controller` from within a test on the test controller.  This
    function arranges for the tests to be run and collects the
    results.  The results are reported as inferior to the test
    context represented by *request*.

    :param: request
        A ``pytest`` request fixture representing the test that is the **test controller**

    :param: target
        A :class:`carthage.SshMixin` with ``pytest`` installed and available and the ``carthage.pytest_plugin`` available.

    :param: pytest_args
        A list of arguments to passed into pytest on the target system.

    :param python_path:
        Set the remote *PYTHONPATH* environment variable to this value.  Typically used when a set of tests is copied to the system to point to directory containings tests.

    :param ssh_agent:
        If *True*, then forward agent credentials.

'''
    if isinstance(pytest_args, str):
        pytest_args = [pytest_args]
    json_frag = f'/tmp/{id(pytest_args)}.json'
    pytest_args = ['--carthage-json=' + json_frag] + pytest_args
    ssh_args = []
    if ssh_agent:
        ssh_args.append('-A')
    if python_path:
        ssh_args.append('PYTHONPATH=' + python_path)
    await target.ssh(*ssh_args,
                     'pytest-3', *pytest_args,
                     _bg=True, _bg_exc=False,
                     _out=sys.stdout)
    json_out = await target.ssh('cat', json_frag)
    report_list = json.loads(json_out.stdout)
    for i in report_list:
        try:
            n = Node.from_parent(name=i['nodeid'], parent=request.node)
        except AttributeError:
            n = Node(name=i['nodeid'], parent=request.node)
            i['nodeid'] = n.nodeid
        report = TestReport(**i)
        capmanager = request.config.pluginmanager.getplugin("capturemanager")
        with capmanager.global_and_fixture_disabled():
            n.ihook.pytest_runtest_logreport(report=report)


__all__ = ['async_test', 'subtest_controller', 'TestTiming']
