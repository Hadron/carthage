import importlib, yaml
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
                    class value(schema_item.type, Injectable):
                        new_value = v
                        def __new__(self, **kwargs):
                            return super().__new__(self, self.new_value, **kwargs)
                            
                    injector.replace_provider(config_key(full_key), value)
                except AttributeError:
                    raise AttributeError("{} is not a config attribute".format(full_key)) from None

        

    def load_yaml(self, y, *, injector = None):
        if injector is None: injector = self._injector
        d = yaml.safe_load(y)
        assert isinstance(d,dict)
        if 'plugins' in d:
            for p in d['plugins']:
                enable_plugin(p)
            del d['plugins']
        self._load(d, injector, self._schema, "")


def enable_plugin(plugin):
    '''
Load and enable a Carthage plugin.

    :param plugin: String representing a module containing a :func:`carthage_plugin` entry point.

'''
    from .. import base_injector
    module = importlib.import_module(plugin)
    plugin = getattr(module, 'carthage_plugin')
    base_injector(plugin)
    from . import inject_config
    inject_config(base_injector)

__all__ = ('ConfigLayout',)
