from .schema import ConfigSchema, config_key, ConfigAccessor
from .layout import ConfigLayout
from . import base
from ..dependency_injection import *

def inject_config(injector):
    injector.replace_provider(ConfigLayout, allow_multiple = True)
    for k in ConfigSchema._schemas:
        injector.replace_provider(config_key(k), partial_with_dependencies(ConfigAccessor, prefix = k+"."), allow_multiple = True)
        

    
__all__ = ("config_key", "ConfigSchema", "ConfigLayout", "inject_config", 'ConfigAccessor')
