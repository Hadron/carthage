# Copyright (C) 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio

from entanglement import SyncServer

from .instrumentation import carthage_registry, CarthageDestination

from carthage.dependency_injection import *
from carthage import ConfigLayout


@inject_autokwargs(injector=Injector,
                   loop=asyncio.AbstractEventLoop,
                   )
class CarthageEntanglement(Injectable):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.loop.call_later(0.2, self.start_server)

    def start_server(self):
        self.config_layout = self.injector(ConfigLayout)
        config = self.config_layout.entanglement
        if config.run_server or config.ws_port:
            self.server = SyncServer(cert=None, port=config.port,
                                     registries=[carthage_registry],
                                     loop=self.loop)

        if config.ws_port:
            import tornado.web
            import tornado.httpserver
            from entanglement.websocket import SyncWsHandler
            self.web_app = tornado.web.Application([(r'/entanglement_ws', SyncWsHandler)])
            self.http_server = tornado.httpserver.HTTPServer(self.web_app)
            self.http_server.listen(int(config.ws_port), address=str(config.ws_address))
            self.web_app.sync_manager = self.server
            self.web_app.find_sync_destination = self.websocket_destination

    def websocket_destination(self, request, *args, **kwargs):
        dest = CarthageDestination()
        return dest
    

@inject(injector=Injector)
def carthage_plugin(injector):
    injector.add_provider(CarthageEntanglement)
    injector.get_instance(CarthageEntanglement)
    carthage_registry.instrument_injector(injector)
    
