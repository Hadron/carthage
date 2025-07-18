# Copyright (C) 2020, 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import collections.abc
import os
import os.path
import hvac
import hvac.exceptions
from carthage import sh
from carthage.config import ConfigSchema, ConfigLayout
from carthage.dependency_injection import *
from carthage.dependency_injection import is_obj_ready
from carthage.config.types import ConfigString, ConfigPath, ConfigLookupPlugin
from carthage.setup_tasks import setup_task
from carthage.ssh import AuthorizedKeysFile, SshAgent, SshKey
from carthage.utils import memoproperty

__all__ = []


class VaultError(RuntimeError):
    pass

__all__ += ['VaultError']


class VaultConfig(ConfigSchema, prefix="vault"):

    #: The address of the vault to contact
    address: ConfigString = None

    #: Path to a CA bundle
    ca_bundle: ConfigString

    #: The path to an ssh key in vault
    ssh_key: ConfigString


vault_token_key = InjectionKey('vault.token')


@inject(
    injector=Injector,
    token=InjectionKey("vault.token", optional=True),
)
class Vault(Injectable):

    def __init__(self,  token=None, **kwargs):
        super().__init__(**kwargs)
        injector = self.injector
        config = injector(ConfigLayout)
        self.vault_config = config.vault
        #: The hvac client for this vault
        self.client = None
        self.setup_client(token)

    def setup_client(self, token):
        '''

        Sets up a client interface to a vault.

            :param token: A token for accessing the vault
            :type token: str or None
        '''

        self.client = hvac.Client(url=self.vault_config. address,
                                  verify=self.vault_config.ca_bundle,
                                  token=token)

    def initialize(self, output_directory, unseal=True,
                   secret_threshold=1,
                   secret_shares=1,
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
                                            secret_shares=secret_shares,
                                            secret_threshold=secret_threshold)
        od = output_directory
        for i, k in enumerate(result['keys_base64']):
            with open(os.path.join(od, f'key.{i}'), "wt") as f:
                f.write(f'{k}')
        with open(os.path.join(od, "token"), "wt") as f:
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

__all__ += ['Vault']


def _apply_config_to_vault(client, config):
    config = dict(config)  # copy so we can mutate
    _apply_policy(client, config.pop('policy', {}))
    _apply_auth(client, config.pop('auth', {}))
    _apply_secrets(client, config.pop('secrets', {}))
    _apply_audit(client, config.pop('audit', {}))
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
    if not auth:
        return
    auth_methods = set(client.sys.list_auth_methods()['data'].keys())
    for a, info in auth.items():
        try:
            desc = info.pop('description', '')
            method_type = info.pop('type', a)
            if a + "/" in auth_methods:
                client.sys.tune_auth_method(a, **info)
            else:
                client.sys.enable_auth_method(method_type=method_type, path=a, description=desc,
                                              config=info)
        except Exception as e:
            raise VaultError(f"Unable to enable auth method at path {a}")


def _apply_secrets(client, secrets):
    if not secrets:
        return
    secrets_engines = set(client.sys.list_mounted_secrets_engines()['data'].keys())
    for s, info in secrets.items():
        try:
            desc = info.pop('description', '')
            backend_type = info.pop('type', s)
            if s + "/" in secrets_engines:
                client.sys.tune_mount_configuration(s, **info)
            else:
                client.sys.enable_secrets_engine(backend_type=backend_type, path=s, description=desc,
                                                 config=info,)
        except Exception as e:
            raise VaultError(f"Unable to enable secrets engine at path {s}")


def _apply_audit(client, audit):
    if not audit:
        return
    audit_devices = set(client.sys.list_enabled_audit_devices()['data'].keys())
    for a, info in audit.items():
        try:
            device_type = info.pop('type', a)
            if a + "/" in audit_devices:
                continue
            client.sys.enable_audit_device(
                device_type=device_type, path=a, options=info)
        except Exception as e:
            raise VaultError(f"Unable to enable auth method at path {a}")


@inject(
    vault=Vault)
class VaultConfigPlugin(ConfigLookupPlugin):

    '''
    Usage in yaml::

        password: {vault:secret/password/{host}:password}

    '''

    def __init__(self, vault):
        self.vault = vault

    def __call__(self, selector):
        client = self.vault.client
        secret, sep, field = selector.rpartition(':')
        if field == "":
            raise SyntaxError("The vault plugin requires a field")
        if secret == "v2":
            try:
                mount, secret, field = field.split(":")
            except ValueError:
                raise SyntaxError(
                    f"Found vault plugin prefix \"v2\" and expected"
                    f" mount:secret:field\" but instead found \"{field}\"."
                ) from None
            result = client.secrets.kv.v2.read_secret(secret, mount)["data"]["data"][field]
            return result

        result = client.read(secret)
        return result['data'][field]


@inject_autokwargs(
    vault=Vault
)
class VaultSshKey(SshKey):

    def __init__(self, **kwargs):
        if 'key_size' in kwargs:
            self._key_size = kwargs.pop('key_size')
        else:
            self._key_size = 2048
        super().__init__(**kwargs)
        config_layout = self.injector(ConfigLayout)
        if not hasattr(config_layout.vault, 'ssh_key'):
            raise AttributeError(
                "\nYou must specify\n\nvault:\n  ssh_key: path/to/key-name\n\nfor this implementation to function")
        self._vault_key_path = config_layout.vault.ssh_key

        self._pubs = None

    def add_to_agent(self, agent):
        assert is_obj_ready(self), f"{self} is not READY"
        r = self.vault.client.read(self._vault_key_path)['data']['data']
        sh.ssh_add('-', _env=agent.agent_environ, _in=r['PrivateKey'])
        del(r)

    @setup_task('gen-key')
    async def generate_key(self):
        pk = sh.openssl(
            'genpkey',
            '-algorithm=RSA',
            '-pkeyopt',
            f'rsa_keygen_bits:{self._key_size}',
            '-outform=PEM',
            _in=None,
            _bg=True,
            _bg_exc=False)
        await pk
        pk_str = pk.stdout
        pubk = sh.openssl('pkey', '-pubout', _in=pk, _bg=True, _bg_exc=False)
        await pubk
        pubk_str = pubk.stdout
        pubs = sh.ssh_keygen('-i', '-m', 'PKCS8', '-f', '/dev/stdin', _in=pubk, _bg=True, _bg_exc=False)
        await pubs
        pubs_str = pubs.stdout

        self.vault.client.write(self._vault_key_path, **dict(data=dict(PrivateKey=pk_str,
                                                                       PublicKey=pubk_str, SshPublicKey=pubs_str)))
        self._pubs = pubs_str
        del(pk, pubk, pubs)

    @generate_key.check_completed()
    def generate_key(self):
        r = self.vault.client.read(self._vault_key_path)
        if r is None:
            return False
        r = r['data']['data']
        if ('PrivateKey' and 'PublicKey' and 'SshPublicKey') not in r.keys():
            return False
        self._pubs = r['SshPublicKey'].replace('\n', '')
        return True

    @memoproperty
    def key_path(self):
        return None

    @memoproperty
    def vault_key_path(self):
        return self._vault_key_path

    @property
    def pubkey_contents(self):
        return self._pubs

@inject_autokwargs(vault=Vault)
class VaultKvMap(Injectable, collections.abc.MutableMapping):

    path:str #: Path within the secrets engine
    mount:str = 'secret'

    def __init__(self, mount=None, path=None, **kwargs):
        if mount is not None:
            self.mount = mount
        if path is not None:
            self.path = path
        super().__init__(**kwargs)
        assert self.path is not None, "Path is mandatory"
        if self.vault.client.read(self.mount+'/config'): 
            self.kv = self.vault.client.secrets.kv.v2
        else:
            self.kv = self.vault.client.secrets.kv.v1
        if not (self.path == '' or self.path.endswith('/')):
            self.path += '/'
            
    def _path(self, p):
        return self.path+p
    
    @property
    def _kwargs(self):
        '''
        Arguments to be added to all calls
        '''
        if self.mount:
            return dict(mount_point=self.mount)
        else: return dict()

    def __getitem__(self, item):
        try:
            response = self.kv.read_secret(self._path(item), **self._kwargs)
        except hvac.exceptions.InvalidPath:
            raise KeyError(item) from None
        match response:
            case {'data': {'data': result}}:
                return result
            case {'data': result}:
                return result

    def __setitem__(self, item, val):
        assert isinstance(val, collections.abc.Mapping)
        self.kv.create_or_update_secret(path=self._path(item), secret=val, **self._kwargs)

    def __len__(self):
        return len(list(iter(self)))

    def __iter__(self):
        res = self.kv.list_secrets(path=self.path, **self._kwargs)
        return res['data']['keys']
        yield

    def  __delitem__(self, item):
        return
    
__all__ += ['VaultKvMap']


from .pki import VaultPkiManager

__all__ += ['VaultPkiManager']

@inject(injector=Injector)
def carthage_plugin(injector):
    injector.add_provider(Vault)
    VaultConfigPlugin.register(injector, "vault")
