# Copyright (C) 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import importlib, yaml
from pathlib import Path
from ..dependency_injection import inject, Injectable, InjectionKey, Injector, partial_with_dependencies


from .schema import config_key, ConfigAccessor, ConfigSchema


@inject(injector = Injector)
class ConfigLayout(ConfigAccessor, Injectable):

    def __init__(self, injector):
        super().__init__(injector, "")
                                      

    def _load(self, d, injector, into, prefix):
        for k,v in d.items():
            full_key = prefix+k
            if full_key in ConfigSchema._schemas:
                if not isinstance(v, dict):
                    raise ValueError("{} should be a dictionary".format(full_key))
                self._load(v, injector, ConfigSchema._schemas[prefix+k], prefix+k+".")
            else:
                try:
                    schema_item = into[k]
                    # We cannot simply use the yaml provided value
                    # because the type class in the schema item may
                    # process the value in construction.  For example
                    # ConfigString does substitutions in construction
                    # but ultimately returns a real string.  We cannot
                    # simply construct the value here, because there
                    # may be forward reference substitutions.  So we
                    # need to defer actually constructing the value
                    # until it is used.  We do this by creating an
                    # injectible class that acts as a dependency
                    # provider for the configuration key.
                    class value(schema_item.type, Injectable):
                        new_value = v
                        def __new__(self, **kwargs):
                            return super().__new__(self, self.new_value, **kwargs)

                        def __init__(self, **kwargs):
                            # This is a bit complicated because of the
                            # various cases.  It appears that if a
                            # class never overrides __init__ at all,
                            # type.__new__ may not end up calling
                            # __init__.  But if any args or kwargs
                            # chain back to object.__init__, then
                            # construction will raise.  Unfortunately,
                            # Injectable needs an __init__.  So if the
                            # underlying type is something like str,
                            # without __init__, then we need to not
                            # pass args along to super().__init__.  If the underlying type is list or something with __init__ provided by C code, we need to pass along a value, but the only way we can detect this is if __init__ has no __function__ attribute.  If the underlying type is something that does have an __init__ written in python we need  to pass along args, but in that case, we detect a __function__ attribute that is not Injectable.__init__.
                            sup_obj = super()
                            if getattr(sup_obj.__init__,'__func__', None) == Injectable.__init__:
                                #Nothing will eat the value, so it will pass along to Object.__init__ and fail
                                sup_obj.__init__(**kwargs)
                            else:
                                sup_obj.__init__(self.new_value, **kwargs)
                            
                    injector.replace_provider(config_key(full_key), value)
                except AttributeError:
                    raise AttributeError("{} is not a config attribute".format(full_key)) from None

        

    def load_yaml(self, y, *, injector = None, path = None):
        if injector is None: injector = self._injector
        if path:
            base_path = Path(path).parent
        else: base_path = Path(y.name).parent
        d = yaml.safe_load(y)
        assert isinstance(d,dict)
        if 'plugins' in d:
            # The plugin loader needs checkout_dir, but we need to
            # load plugins before loading config because plugins can
            # introduce new schema.  This is not strictly correct
            # because the loaded value for checkout_dir may include
            # substitutions to other items that are also in the
            # config.  Don't do that.
            if 'checkout_dir' in d:
                self.checkout_dir = d['checkout_dir']
            for p in d['plugins']:
                if (not ':' in p) and (p == '.' or '/' in p):
                    p = base_path.joinpath(p)
                enable_plugin(p)
            del d['plugins']
        self._load(d, injector, self._schema, "")


def enable_plugin(plugin):
    '''
Load and enable a Carthage plugin.

    :param plugin: String representing a module containing a :func:`carthage_plugin` entry point.

'''
    from .. import base_injector
    from ..plugins import load_plugin
    base_injector(load_plugin, plugin)
    from . import inject_config
    inject_config(base_injector)

__all__ = ('ConfigLayout',)
