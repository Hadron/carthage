from carthage import *

from pyVim.connect import Connect, SmartConnect, Disconnect
from ssl import create_default_context

@inject(config = ConfigLayout)
class VmwareConnection(Injectable):

    def __init__(self, config):
        self.config = config.vmware
        ssl_context = create_default_context()
        if self.config.validate_certs is False:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = 0
        self.connection = None
        self.connection = SmartConnect(host=self.config.hostname,
                                  user=self.config.username,
                                  pwd=self.config.password,
                                  sslContext=ssl_context)
        self.content = self.connection.content

    def close(self):
        if self.connection:
            Disconnect(self.connection)
            self.connection = None

    def __del__(self):
        self.close()
