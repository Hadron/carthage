
import os
import os.path
import hvac
from .config import ConfigSchema, ConfigLayout
from .dependency_injection import *
from .config.types import ConfigString, ConfigPath

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
    
