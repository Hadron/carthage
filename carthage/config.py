import importlib, yaml
from .dependency_injection import inject, Injectable, InjectionKey, Injector, partial_with_dependencies

class ConfigDefaults:

    def add_config(self, c):
        '''
        Add additional config settings.

        :param: c
            A dictionary of key-value pairs.  The keys will become valid config settings with the value being the initial default.  If the value is a dict, it will be merged with any existing settings with that prefix.

'''
        def merge(src, target):
            for k,v in src.items():
                if k in target:
                    orig_val = target[k]
                    if isinstance(orig_val, dict):
                        if isinstance(v, dict):
                            merge(v, orig_val)
                        else:
                            raise ValueError("{k} needs to be a dictionary to fit into existing ConfigSection".format(k))
                else: target[k] = v
        merge(c, self.defaults)

    def sections(self):
        def descend(prefix, d):
            for k,v in d.items():
                if isinstance(v, dict):
                    yield prefix+k
                    yield from descend(prefix+k+'.', v)
        return descend("", self.defaults)

    def __init__(self):
        self.defaults = {}

config_defaults = ConfigDefaults()

@inject(injector = Injector)
class ConfigIterator:

    def __init__(self, injector, prefix):
        self._injector = injector
        self._prefix = prefix
        self._defaults = config_defaults.defaults
        for k in prefix.split("."):
            if k == "": continue
            self._defaults = self._defaults[k]
            

    def __getattr__(self, k):
        try:
            return self._injector.get_instance(config_key(self._prefix+k))
        except KeyError:
            try:
                v = self._defaults[k]
                assert not isinstance(v, dict) # There should be an InjectionKey for all sections
                return v
            except KeyError:
                raise AttributeError("{} is not a configuration key".format(self._prefix+k)) from None

    def __setattr__(self, k, v):
        if k.startswith('_'): return super().__setattr__(k, v)
        if not hasattr(self, k):
            raise AttributeError("{} is not a configuration key".format(self._prefix+k))
        if isinstance(v, dict):
            raise ValueError("You cannot set a configuration key to a dictionary")
        self._injector.replace_provider(config_key(self._prefix+k), v)


                
@inject(injector = Injector)
class ConfigLayout(ConfigIterator, Injectable):

    def __init__(self, injector):
        super().__init__(injector, "")
                                      

    def _load(self, d, injector, into, prefix):
        for k,v in d.items():
            full_key = prefix+k
            try:
                v_orig = into[k]
                if isinstance(v_orig, dict):
                    if not isinstance(v, dict):
                        raise ValueError("{} should be a dictionary".format(full_key))
                    self._load(v, injector, v_orig, prefix+k+".")
                else:
                    injector.replace_provider(config_key(full_key), v)
            except AttributeError:
                raise AttributeError("{} is not a config attribute".format(full_key)) from None

        

    def load_yaml(self, y, *, injector = None):
        if injector is None: injector = self._injector
        d = yaml.load(y)
        assert isinstance(d,dict)
        if 'plugins' in d:
            for p in d['plugins']:
                enable_plugin(p)
            del d['plugins']
        self._load(d, injector, config_defaults.defaults, "")

config_defaults.add_config(dict(
    image_dir = "/srv/images/test",
    vm_image_dir = "/srv/images/test/vm",
    vm_image_size = 20000000000,
        base_container_image = "/usr/share/hadron-installer/hadron-container-image.tar.gz",
    base_vm_image = "/usr/share/hadron-installer/direct-install-efi.raw.gz",
    container_prefix = 'carthage-',
    state_dir ="/srv/images/test/state",
    min_port = 9000,
    max_port = 9500,
    hadron_operations = "/home/hartmans/hadron-operations",
    delete_volumes = False,
    external_vlan_id= 0,
    vlan_min = 1,
    vlan_max = 4094,
))


def config_key(k):
    return InjectionKey("config/"+k)

def inject_config(injector):
    for k in config_defaults.sections():
        injector.replace_provider(config_key(k), partial_with_dependencies(ConfigIterator, prefix = k+"."), allow_multiple = True)
    injector.replace_provider(ConfigLayout, allow_multiple = True)
        

def enable_plugin(plugin):
    '''
Load and enable a Carthage plugin.

    :param plugin: String representing a module containing a :func:`carthage_plugin` entry point.

'''
    from . import base_injector
    module = importlib.import_module(plugin)
    plugin = getattr(module, 'carthage_plugin')
    base_injector(plugin)
    inject_config(base_injector)
    
__all__ = ("config_key", "config_defaults", "ConfigLayout", "inject_config")
