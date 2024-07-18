# Copyright (C) 2019, 2020, 2021, 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import importlib
import logging
import yaml
from pathlib import Path
from ..dependency_injection import inject, Injectable, InjectionKey, Injector, partial_with_dependencies
import carthage

from .schema import config_key, ConfigAccessor, ConfigSchema


@inject(injector=Injector)
class ConfigLayout(ConfigAccessor, Injectable):

    def __init__(self, injector):
        super().__init__(injector, "")

    def _load(self, d, injector, into, prefix):
        for k, v in d.items():
            full_key = prefix + k
            if full_key in ConfigSchema._schemas:
                if not isinstance(v, dict):
                    raise ValueError("{} should be a dictionary".format(full_key))
                self._load(v, injector, ConfigSchema._schemas[prefix + k], prefix + k + ".")
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

                    class ConfigValue(schema_item.type, Injectable):
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
                            # pass args along to super().__init__.  If the underlying type is list or
                            # something with __init__ provided by C code, we need to pass along a
                            # value, but the only way we can detect this is if __init__ has no
                            # __function__ attribute.  If the underlying type is something that does
                            # have an __init__ written in python we need  to pass along args, but in
                            # that case, we detect a __function__ attribute that is not
                            # Injectable.__init__.
                            sup_obj = super()
                            if getattr(sup_obj.__init__, '__func__', None) == Injectable.__init__:
                                # Nothing will eat the value, so it will pass along to Object.__init__ and fail
                                sup_obj.__init__(**kwargs)
                            else:
                                sup_obj.__init__(self.new_value, **kwargs)

                    injector.replace_provider(config_key(full_key), ConfigValue)
                except AttributeError:
                    raise AttributeError("{} is not a config attribute".format(full_key)) from None

    def load_yaml(self, y, *, injector=None, path=None,
                  ignore_import_errors=False):
        '''
        :param ignore_import_errors: If true, then loading a plugin will not fail simply because the plugin's python code  raises an error.  This is intended to allow introspection of plugin metadata to determine plugin dependencies; actually trying to use a plugin that has raised an error on load is unlikely to work.
        '''
        if injector is None:
            injector = self._injector
        if path:
            base_path = Path(path).parent
        else:
            base_path = Path(y.name).parent
        d = yaml.safe_load(y)
        assert isinstance(d, dict)
        from .types import ConfigPath
        # The plugin loader needs checkout_dir, but we need to
        # load plugins before loading config because plugins can
        # introduce new schema.  This is not strictly correct
        # because the loaded value for checkout_dir may include
        # substitutions to other items that are also in the
        # config.  Don't do that.
        for early_key in ('checkout_dir', 'base_dir'):
            if early_key not in d:
                continue
            setattr(self, early_key,
                    injector(ConfigPath, d[early_key]))
        if 'debug_categories' in d:
            for category in d.pop('debug_categories'):
                logging.getLogger(category).setLevel(10)
        if 'plugin_mappings' in d:
            plugin_mappings = injector.get_instance(carthage.plugins.PluginMappings)
            assert isinstance(d['plugin_mappings'], list), "plugin_mappings is a list of mappings"
            for mapping in d.pop('plugin_mappings'):
                plugin_mappings.add_mapping(mapping)
        if 'plugins' in d:
            for p in d['plugins']:
                if (not ':' in p) and (p == '..' or p == '.' or '/' in p):
                    p = base_path.joinpath(p)
                enable_plugin(p, ignore_import_errors=ignore_import_errors)
            del d['plugins']
        if 'include' in d:
            for include in d['include']:
                include = injector(ConfigPath, include)
                include = base_path.joinpath(include)
                with include.open("rt") as include_file:
                    self.load_yaml(include_file, ignore_import_errors=ignore_import_errors)
            del d['include']
        try: self._load(d, injector, self._schema, "")
        except KeyError:
            if not ignore_import_errors: raise
            # We can fail to get new schema defined because of an
            # import that failed, so also ignore KeyError from _load.


def enable_plugin(plugin, ignore_import_errors=False):
    '''
Load and enable a Carthage plugin.

    :param plugin: String representing a module containing a :func:`carthage_plugin` entry point.

'''
    from .. import base_injector
    from ..plugins import load_plugin
    base_injector(load_plugin, plugin, ignore_import_errors=ignore_import_errors)
    from . import inject_config
    inject_config(base_injector)


__all__ = ('ConfigLayout',)
