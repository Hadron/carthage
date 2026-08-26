# Copyright (C) 2023, 2026, Hadron Industries, Inc.
# Carthage is free software; you can redistribute and/or modify
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


@inject_autokwargs(injector=Injector)
class CarthageEntanglement(Injectable):

    '''Entanglement synchronization for the carthage layout.

    When the config requires a sync server (``entanglement.run_server`` or
    ``entanglement.ws_port``), the server is started in response to the
    ``loop_ready`` event, which is emitted toward
    ``InjectionKey(asyncio.AbstractEventLoop)`` (and ``InjectionKey(Injector)``)
    by :meth:`carthage_main_register_loop <carthage.utils.carthage_main_register_loop>`
    once the event loop is registered on the base injector.
    '''

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config = self.injector(ConfigLayout).entanglement
        # Events only propagate upward through the injector hierarchy, so the
        # listener must be attached to the base (root) injector, on which the
        # loop_ready event is emitted.
        injector = self.injector
        while injector.parent_injector:
            injector = injector.parent_injector
        injector.add_event_listener(
            InjectionKey(asyncio.AbstractEventLoop),
            {'loop_ready'}, self.on_loop_ready)
        if injector.loop is not None:
            # A loop was registered before this plugin loaded; the event
            # already fired, so start the server now.
            self.on_loop_ready(key=InjectionKey(asyncio.AbstractEventLoop),
                               event='loop_ready', target=injector.loop)

    @property
    def sync_server_needed(self):
        return bool(self.config.run_server or self.config.ws_port)

    def on_loop_ready(self, key, event, target, **kwargs):
        self.start_server(loop=target)

    def start_server(self, loop):
        config = self.config
        if self.sync_server_needed:
            self.server = SyncServer(cert=None, port=config.port,
                                     registries=[carthage_registry],
                                     loop=loop)

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
