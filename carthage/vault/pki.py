# Copyright (C) 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
from carthage import *
import carthage.pki
from . import Vault

__all__ = []

async def run_in_executor(func, *args):
    return await asyncio.get_event_loop().run_in_executor(None, func, *args)

@inject_autokwargs(
    vault=Vault)
class VaultPkiManager(carthage.pki.PkiManager):

    '''
    A PKI manager mounted corresponding to a vault pki secrets engine.
    '''
    path:str = 'pki' #: Path at which secrets engine is mounted
    role:str = None #:Role to issue against.

    def __init__(self, *, path=None, role=None, **kwargs):
        if path is not None:
            self.path = Path
        if role is None and self.role is None:
            raise TypeError('role must be set in the constructor or subclass')
        if role:
            self.role = role
        super().__init__(**kwargs)

    async def issue_credentials(self, hostname:str, tag:str):
        def cb():
            return self.vault.client.write(
                f'{self.path}/issue/{self.role}',
                common_name=hostname
                )
        result = await run_in_executor(cb)
        key = result['data']['private_key']
        cert = result['data']['certificate']
        return key, cert
    
    async def trust_store(self):
        def cb():
            res = self.vault.client.read(f'{self.path}/ca/pem')
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
            return res['data']['certificate']
    
        for key in await run_in_executor(cb_list):
            yield await run_in_executor(cb_cert, key)
    
__all__ += ['VaultPkiManager']
