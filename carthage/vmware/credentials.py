# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

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
