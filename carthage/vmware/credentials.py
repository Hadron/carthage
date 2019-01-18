from ..config import config_defaults, config_key

config_defaults.add_config({
    'vmware': {
        'hostname': None,
        'username': None,
        'password': None,
        'validate_certs': False
        }})

#: Injection key to get Vmware credential configuration.  Only assume that username, hostname, password and verify_certs are set with this key as a dependency.
vmware_credentials = config_key('vmware')

__all__ = ['vmware_credentials']
