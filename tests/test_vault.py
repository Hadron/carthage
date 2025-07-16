# Copyright (C) 2024-2025, Hadron Industries, Inc.
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
                pki={'type':'pki'},
                v1={'type': 'kv',
                    'version':1},
                v2={'type': 'kv-v2',},
            ),
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

@pytest.mark.parametrize(
    'cache_mount',
    [None, 'v1', 'v2'])
@async_test
async def test_pki_issue(ainjector, cache_mount):
    # We override the pki manager so we can set cache_mount
    class PkiClass(VaultPkiManager):
        role = 'role'
        locals()['cache_mount'] = cache_mount
    ainjector.replace_provider(InjectionKey(VaultPkiManager), PkiClass)
    pki = await ainjector.get_instance_async(VaultPkiManager)
    await pki.issue_credentials('evil.com', 'tag')
    # And try issuing again for cache.
    pki.hostname_tags.clear()
    await pki.issue_credentials('evil.com', 'tag')
    

@async_test
async def test_pki_certs(ainjector):
    pki = await ainjector.get_instance_async(VaultPkiManager)
    key_1, cert_1 = await pki.issue_credentials('internet.com', 'tag')
    key_2,cert_2 = await pki.issue_credentials('dns.net', 'tag')
    certs = [c async for c in pki.certificates()]
    assert any(map(lambda c: cert_1[0:200] in c, certs))
    assert any(map(lambda c: cert_2[0:200] in c, certs))
    
@async_test
async def test_trust_store(ainjector):
    pki = await ainjector.get_instance_async(VaultPkiManager)
    trust_store = await pki.trust_store()
    certs = [c async for c in  trust_store.trusted_certificates()]
    await trust_store.ca_file()
    

@pytest.mark.parametrize(
    'mount',
    ['v1',
     'v2'])
@async_test
async def test_secrets(ainjector, mount):
    path ='foo'
    mapping = await ainjector(VaultKvMap,
                              mount=mount, path=path)
    mapping['item'] = dict(baz=20)
    assert mapping['item']['baz'] == 20
    for i in mapping: pass

@async_test
async def test_pki_indexer(ainjector):
    try:
        from carthage_base.pki_indexer import PkiIndexer
    except ImportError:
        pytest.skip('carthage-base is not available')
    pki = await ainjector.get_instance_async(VaultPkiManager)
    key, cert = await pki.issue_credentials('evil.com', 'tag')
    indexer = await ainjector(PkiIndexer)
    indexer._process_bytes('cert', cert)
    indexer._process_bytes('key', key)
    await indexer.validate()
    key2, cert2 = await indexer.issue_credentials('evil.com', 'tag')
    trust_store = await indexer.trust_store()
    assert len([c async for c in trust_store.trusted_certificates()]) == 1
    
