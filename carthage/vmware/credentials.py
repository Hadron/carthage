# Copyright (C) 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from ..config import ConfigSchema, config_key


class CredentialsSchema(ConfigSchema, prefix="vmware"):
    hostname: str
    username: str = "{vault:secret/password/{vmware.hostname}:username}"
    password: str = "{vault:secret/password/{vmware.hostname}:password}"
    proxy: str = None
    validate_certs: bool = False


#: Injection key to get Vmware credential configuration.  Only assume that username, hostname, password and verify_certs are set with this key as a dependency.
vmware_credentials = config_key('vmware')

__all__ = ['vmware_credentials']
