# Copyright (C) 2022,  2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.config import ConfigSchema
from .server import CarthageEntanglement, carthage_plugin
from .instrumentation import *
from . import instrumentation
from carthage import inject, Injector

class EntanglementConfig(ConfigSchema, prefix='entanglement'):

    run_server: bool = True
    ws_port:int = None
    ws_address:str = "127.0.0.1"
    port:int = 39102
    
__all__ = instrumentation.__all__
__all__ += ['CarthageEntanglement', 'carthage_plugin']
