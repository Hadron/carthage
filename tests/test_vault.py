# Copyright (C) 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import pytest
import sys
import time
from carthage.pytest import *
from carthage import *
try:
    from carthage.vault import *
except ImportError: pass




@pytest.fixture(scope='module')
def vault():
    '''
    Start a vault in dev mode
    '''
    try:
        import carthage.vault
        from sh import vault as vault_cmd
    except Exception:
        pytest.skip('vault not installed')
    vault_proc = vault_cmd('server', '-dev', _bg=True, _bg_exc=False, _out=sys.stdout,
                           _err_to_out=True)
    time.sleep(1)
    yield
    vault_proc.kill()

@pytest.fixture()
def ainjector(ainjector, vault):
    ainjector.add_provider(ConfigLayout)
    cl = ainjector.get_instance(ConfigLayout)
    cl.vault.address = 'http://127.0.0.1:8200/'
    ainjector.add_provider(Vault)
    vault = ainjector.get_instance(Vault)
    vault.apply_config(
        {
            'secrets':dict(
                pki={'type':'pki'}),
            'pki/root/generate/internal': dict(
                ttl='120h',
                common_name='ROOT'),
            'pki/roles/role': dict(
                allow_any_name=True,
                ttl='30h'
                ),
            })
    class pki(VaultPkiManager):
        role = 'role'
    ainjector.add_provider(pki)
    yield ainjector

@async_test
async def test_pki_issue(ainjector):
    pki = await ainjector.get_instance_async(VaultPkiManager)
    await pki.issue_credentials('evil.com', 'tag')
    

@async_test
async def test_pki_certs(ainjector):
    pki = await ainjector.get_instance_async(VaultPkiManager)
    key_1, cert_1 = await pki.issue_credentials('internet.com', 'tag')
    key_2,cert_2 = await pki.issue_credentials('dns.net', 'tag')
    certs = [c async for c in pki.certificates()]
    assert cert_1 in certs
    assert cert_2 in certs
    
@async_test
async def test_trust_store(ainjector):
    pki = await ainjector.get_instance_async(VaultPkiManager)
    trust_store = await pki.trust_store()
    certs = [c async for c in  trust_store.trusted_certificates()]
    await trust_store.ca_file()
    
