# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


from carthage.pki import PkiManager
from carthage.config import ConfigLayout
from carthage import base_injector, Injector
import os
import os.path
import pytest
import shutil
import carthage.sh


@pytest.fixture(scope='module')
def injector():
    injector = base_injector(Injector)
    cl = injector.get_instance(ConfigLayout)
    cl.state_dir = os.path.join(os.path.dirname(__file__), "test_state")
    try:
        carthage.sh.entanglement_pki('--help')
    except BaseException:
        pytest.skip('entanglement_pki not installed')
    yield injector
    shutil.rmtree(cl.state_dir)


@pytest.fixture
def pki_manager(injector):
    return injector(PkiManager)


def test_certify(pki_manager):
    creds = pki_manager.credentials('photon.cambridge')
    assert 'CERTIFICATE' in creds
    assert 'CERTIFICATE' in pki_manager.ca_cert
