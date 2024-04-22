# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *

from pyVim.connect import Connect, SmartConnect, Disconnect
from ssl import create_default_context

import logging
from urllib.parse import urlparse

@inject(config=ConfigLayout)
class VmwareConnection(Injectable):

    def __init__(self, config):
        self.config = config.vmware
        ssl_context = create_default_context()
        if self.config.validate_certs is False:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = 0
        kwargs = dict(host=self.config.hostname,
                      user=self.config.username,
                      pwd=self.config.password,
                      sslContext=ssl_context)
        if self.config.proxy:
            r = urlparse(self.config.proxy)
            if r.scheme != 'http':
                raise RuntimeError(f"unsupported proxy scheme '{r.scheme}' in proxy string '{self.config.proxy}'; current support is only for 'http'")
            kwargs['httpProxyHost'] = r.hostname
            kwargs['httpProxyPort'] = r.port
        self.connection = None
        logging.debug(f'connecting to {self.config.hostname} as {self.config.username} using {kwargs}')
        self.connection = SmartConnect(**kwargs)
        self.content = self.connection.content
        logging.debug(f'connected to {self.config.hostname}')

    def close(self):
        if self.connection:
            Disconnect(self.connection)
            self.connection = None

    def __del__(self):
        self.close()
