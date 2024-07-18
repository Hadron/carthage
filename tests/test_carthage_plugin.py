# Copyright (C) 2018, 2019, 2021, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import os.path
import pytest
from carthage.pytest import *
from carthage import base_injector, AsyncInjector, inject, ConfigLayout
import carthage.ssh
'''This is a test for the pytest plugins, not for the carthage plugin mechanism'''

from machine_mock import Machine

resource_dir = os.path.dirname(__file__)


def test_test_parameters(test_parameters):
    return True


@async_test
async def test_async_test():
    return True


@async_test
async def test_async_test_with_loop(loop):
    assert loop


@async_test
@inject(config=ConfigLayout)
def test_carthage_injection(config, ainjector):
    assert config.delete_volumes == False


@async_test
@inject(ssh_key=carthage.ssh.SshKey)
async def test_mock_machine(ssh_key):
    m = Machine("Test machine")
    await ssh_key.rsync(os.path.join(resource_dir, 'test_carthage_plugin.py'),
                        m.rsync_path('/'))
    assert os.path.exists(os.path.join(
        m.path, "test_carthage_plugin.py"))


@async_test
@inject(ssh_key=carthage.ssh.SshKey)
async def test_carthage_controller(ssh_key, request, capsys):
    m = Machine('carthage-inner')
    await ssh_key.rsync(os.path.join(
        resource_dir,
        "inner_plugin_test.py"),
        m.rsync_path('/'))
    await ssh_key.rsync(
        os.path.join(resource_dir, "inner_conftest.py"),
        m.rsync_path("/conftest.py"))
    await subtest_controller(request, m, "inner_plugin_test.py")
