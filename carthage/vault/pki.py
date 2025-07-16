# Copyright (C) 2024, 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
from carthage import *
import carthage.pki
import carthage.pki_utils as pki_utils
from . import Vault, VaultKvMap

__all__ = []

async def run_in_executor(func, *args):
    return await asyncio.get_event_loop().run_in_executor(None, func, *args)

@inject_autokwargs(
    vault=Vault,
    injector=Injector)
class VaultPkiManager(carthage.pki.PkiManager):

    '''
    A PKI manager mounted corresponding to a vault pki secrets engine.
    Optionally caches issued certificates (and keys) in a kv secrets engine so CI pipelines can avoid certificate churn. If *cache_mount* is set, then certificates and keys are stored and reused so long as they are not too close to expiration.
    '''

    path:str = 'pki' #: Path at which secrets engine is mounted
    role:str = None #:Role to issue against.
    cache_mount: str = None #: The mount of a kv engine against which to cache certificates
    cache_path: str = '' #: Path within cache_mount

    def __init__(self, *, path=None, role=None,
                 cache_mount:str = None,
                 cache_path:str = None,
                 **kwargs):
        if path is not None:
            self.path = path
        if role is None and self.role is None:
            raise TypeError('role must be set in the constructor or subclass')
        if role:
            self.role = role
        if cache_mount is not None:
            self.cache_mount = cache_mount
        if cache_path is not None:
            self.cache_path = cache_path
        super().__init__(**kwargs)
        if self.cache_mount:
            self.kv_map = self.injector(VaultKvMap, mount=self.cache_mount, path=self.cache_path)
        else:
            self.kv_map = None
        self.hostname_tags = {}

    async def issue_credentials(self, hostname:str, tag:str):
        def cb():
            if self.kv_map is not None:
                cache_key = f'{hostname}:{tag}'
                cached_result = self.kv_map.get(cache_key)
                if cached_result and not pki_utils.certificate_is_expired(
                        cached_result['certificate'], fraction_left=0.5):
                    return cached_result
                
            result =  self.vault.client.write(
                f'{self.path}/issue/{self.role}',
                common_name=hostname
                )
            result = result['data']
            if self.kv_map is not None:
                self.kv_map[cache_key] = result
            return result
        tags = self.hostname_tags.setdefault(hostname, set())
        if tag in tags:
            raise ValueError(f'Tag {tag} for {hostname} is duplicated')
        tags.add(tag)
        result = await run_in_executor(cb)
        key = result['private_key']
        cert = result['certificate']
        # I think that certificate includes ca_chain
        #try:
        #cert +='\n'+result['ca_chain']
#        except KeyError: pass
        try:
            cert += '\n'+result['issuing_ca']
        except KeyError:pass
        cert = pki_utils.x509_annotate(cert)
        return key, cert
    
    async def trust_store(self):
        def cb():
            res = self.vault.client.read(f'{self.path}/ca_chain')
            return res.text
        ca_pem = await run_in_executor(cb)
        return await self.ainjector(
            carthage.pki.SimpleTrustStore,
            'vault_'+self.path,
            dict(ca=ca_pem))

    async def certificates(self):
        def cb_list():
            res = self.vault.client.list(f'{self.path}/certs')
            return res['data']['keys']
        def cb_cert(key):
            res = self.vault.client.read(f'{self.path}/cert/{key}')
            return pki_utils.x509_annotate(res['data']['certificate'])
    
        for key in await run_in_executor(cb_list):
            yield await run_in_executor(cb_cert, key)
    
__all__ += ['VaultPkiManager']
