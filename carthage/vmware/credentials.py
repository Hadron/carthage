from ..config import ConfigSchema, config_key

class CredentialsSchema(ConfigSchema, prefix = "vmware"):
    hostname: str
    username: str = "{vault:secret/password/{vmware.hostname}:username}"
    password: str = "{vault:secret/password/{vmware.hostname}:password}"
    validate_certs: bool = False

#: Injection key to get Vmware credential configuration.  Only assume that username, hostname, password and verify_certs are set with this key as a dependency.
vmware_credentials = config_key('vmware')

__all__ = ['vmware_credentials']
