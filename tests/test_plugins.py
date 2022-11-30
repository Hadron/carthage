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
from carthage.plugins import load_plugin
from pathlib import Path


dir = Path(__file__).parent


@pytest.fixture()
def injector(ainjector):
    return ainjector.injector


def test_load_vmware(injector):
    injector(load_plugin, "carthage.vmware")


def test_dir_plugin(injector):
    injector(load_plugin, dir / "test_plugin")
