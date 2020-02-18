# Copyright (C) 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


import os
import os.path
import hvac
from .config import ConfigSchema, ConfigLayout
from .dependency_injection import *
from .config.types import ConfigString, ConfigPath

class VaultError(RuntimeError): pass
class VaultConfig(ConfigSchema, prefix = "vault"):

    #: The address of the vault to contact
    address: ConfigString = None

    #: Path to a CA bundle
    ca_bundle : ConfigString

vault_token_key = InjectionKey('vault.token')

@inject( config = ConfigLayout,
         ainjector = AsyncInjector)
class Vault(AsyncInjectable):

    def __init__(self, config, ainjector):
        self.vault_config = config.vault
        self.ainjector = ainjector
        super().__init__()
        #: The hvac client for this vault
        self.client = None


    def setup_client(self, token):

        '''

        Sets up a client interface to a vault.

            :param token: A token for accessing the vault
            :type token: str or None
        '''
    
        self.client = hvac.Client(url = self.vault_config. address,
                                  verify = self.vault_config.ca_bundle,
                                  token = token)
        

    async def async_ready(self):
        try:
            token = await self.ainjector.get_instance_async(vault_token_key)
        except KeyError: token = None
        self.setup_client(token = token)
        return await super().async_ready()

    def initialize(self, output_directory, unseal = True,
                   secret_threshold = 1,
                   secret_shares = 1,
                   **kwargs):

        '''Initialize a vault.
Writes unseal keys and root token to the given output directory, which hopefully is ephemeral.
        
        The output directory will contain:

        * ``key.``n: Unseal keys starting at index 0

        * token: root token

        '''
        if not os.path.exists(output_directory):
            os.makedirs(output_directory)
        if not os.path.isdir(output_directory):
            raise ValueError(f'{output_directory} is not a directory')
        result = self.client.sys.initialize(**kwargs,
                                            secret_shares = secret_shares,
                                            secret_threshold = secret_threshold)
        od = output_directory
        for i, k in enumerate(result['keys_base64']):
            with open(os.path.join(od, f'key.{i}'), "wt") as f:
                f.write(f'{k}')
        with open(os.path.join( od, "token"), "wt") as f:
            f.write(result['root_token'])
        if unseal:
            for i in range(secret_threshold):
                self.client.sys.submit_unseal_key(result['keys_base64'][i])
        self.setup_client(result['root_token'])
        return result
    
    def apply_config(self, config):
        '''
        Apply configuration such as policies or  authentication methods to vault.
        
        :param dict config: A configuration dictionary; see :ref:`vault:config` for details.
        '''
        _apply_config_to_vault(self.client, config)
        return

def _apply_config_to_vault(client, config):
    config = dict(config) #copy so we can mutate
    _apply_policy(client, config.pop('policy', {}))
    _apply_auth(client, config.pop('auth', {}))
    _apply_secrets(client, config.pop('secrets', {}))
    for k in config:
        try:
            client.write(k, **config[k])
        except Exception as e:
            raise VaultError(f"failed to write {k}") from e

def _apply_policy(client, policy):
    for p in policy:
        try:
            client.sys.create_or_update_policy(p, policy[p])
        except Exception as e:
            raise VaultError(f"Unable to create Policy {p}")

def _apply_auth(client, auth):
    auth_methods = set(client.sys.list_auth_methods()['data'].keys())
    for a, info in auth.items():
        if a+"/" in auth_methods: continue
        try:
            desc = info.pop('description', '')
            method_type = info.pop('type', a)
            client.sys.enable_auth_method(method_type = method_type, path = a, description = desc,
                                          config = info)
        except Exception as e:
            raise VaultError(f"Unable to enable auth method at path {a}")

def _apply_secrets(client, secrets):
    secrets_engines = set(client.sys.list_mounted_secrets_engines()['data'].keys())
    for s, info in secrets.items():
        if s+"/" in secrets_engines: continue
        try:
            desc = info.pop('description', '')
            backend_type = info.pop('type', s)
            client.sys.enable_secrets_engine(backend_type = backend_type, path = s, description = desc,
                                          config = info)
        except Exception as e:
            raise VaultError(f"Unable to enable secrets engine at path {s}")

        
            
