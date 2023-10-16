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
