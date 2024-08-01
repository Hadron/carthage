# Copyright (C) 2018, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import os
import os.path
import pytest
import shutil
from pathlib import Path

from carthage.pki import EntanglementPkiManager, PemBundleTrustStore
from carthage.config import ConfigLayout
from carthage import base_injector, Injector, AsyncInjector
from carthage.pytest import *
import carthage.sh
resource_dir = Path(__file__).parent.joinpath('resources')


@pytest.fixture(scope='module')
def ainjector():
    injector = base_injector(Injector)
    cl = injector(ConfigLayout)
    cl.state_dir = os.path.join(os.path.dirname(__file__), "test_state")
    try:
        carthage.sh.entanglement_pki('--help', _bg=False)
    except BaseException:
        pytest.skip('entanglement_pki not installed')
    ainjector = injector(AsyncInjector)
    yield ainjector
    shutil.rmtree(cl.state_dir)


@pytest.fixture
def pki_manager(ainjector, loop):
    return loop.run_until_complete(ainjector(EntanglementPkiManager))


@async_test
async def test_certify(pki_manager):
    key, cert = await pki_manager.issue_credentials('photon.cambridge', 'photon.cambridge machine cert')
    assert 'CERTIFICATE' in cert
    assert 'CERTIFICATE' in pki_manager.ca_cert

@async_test
async def test_pem_bundle_trust_store(ainjector):
    pem_bundle = await ainjector(
        PemBundleTrustStore, 'trust_root', resource_dir/'cacerts.pem')
    tagged_certificates = {tag:cert async for tag, cert in pem_bundle.trusted_certificates()}
    assert len(tagged_certificates) == 146
