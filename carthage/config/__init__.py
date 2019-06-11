# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .schema import ConfigSchema, config_key, ConfigAccessor
from .layout import ConfigLayout
from . import base
from ..dependency_injection import *

def inject_config(injector):
    injector.replace_provider(ConfigLayout, allow_multiple = True)
    for k in ConfigSchema._schemas:
        injector.replace_provider(config_key(k), partial_with_dependencies(ConfigAccessor, prefix = k+"."), allow_multiple = True)
        

    
__all__ = ("config_key", "ConfigSchema", "ConfigLayout", "inject_config", 'ConfigAccessor')
