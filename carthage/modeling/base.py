from .implementation import *
from carthage.dependency_injection import * #type: ignore
import typing
import carthage.network
from .utils import *
__all__ = []

class injector_access:

    '''Usage::

        val = injector_access("foo")

    At runtime, ``model_instance.val`` will be memoized to ``model_instance.injector.get_instance("foo")``.

    '''


    key: InjectionKey
    name: str

    def __init__(self, key):
        if not isinstance(key, InjectionKey):
            key = InjectionKey(key, _ready = False)
        if key.ready is None: key = InjectionKey(key, _ready = False)
        #: The injection key to be accessed.
        self.key = key
        # Our __call__method needs an injector
        inject(injector = Injector)(self)


    def __get__(self, inst, owner):
        res = inst.injector.get_instance(self.key)
        setattr(inst, self.name, res)
        return res

    def __set_name__(self, owner, name):
        self.name = name

    def __call__(self, injector: Injector):
        return injector.get_instance(self.key)
    def __repr__(self):
        return f'injector_access({repr(self.key)})'

__all__ += ['injector_access']

@inject(injector = Injector)
class InjectableModel(Injectable, metaclass = InjectableModelType):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        dependency_providers: typing.Mapping[typing.Any,DependencyProvider] = {}
        # This is complicated because we want to reuse the same
        # DependencyProvider when registering the same value more than
        # once so that instantiations alias and we don't accidentally
        # get multiple instances of the same type providing related
        # but different keys.
        for k,info in self.__class__.__initial_injections__.items():
            v, options = info
            try:
                dp = dependency_providers[v]
            # TypeError: unhashable
            except (KeyError, TypeError):
                dp = DependencyProvider(
                    v, close = options['close'],
                    allow_multiple = options['allow_multiple'])
                try: dependency_providers[v] = dp
                except TypeError: pass
            if 'globally_unique' in options:
                options = dict(options)
                del options['globally_unique']
            try:
                self.injector.add_provider(k, dp, replace = True, **options)
            except Exception as e:
                raise RuntimeError(f'Failed registering {v} as provider for {k}') from e

        for c in reversed(self.__class__.__mro__):
            if isinstance(c, ModelingBase) and hasattr(c, '_callbacks'):
                for cb in c._callbacks:
                    cb(self)

    def __init_subclass__(cls, *args, template = False, **kwargs):
        super().__init_subclass__(*args, **kwargs)
        
__all__ += ['InjectableModel']

class NetworkModel(carthage.Network, InjectableModel, metaclass = ModelingContainer):

    def __init__(self, **kwargs):
        kwargs.update(gather_from_class(self, 'name', 'vlan_id'))
        super().__init__(**kwargs)
        if hasattr(self,'bridge_name'):
            self.ainjector.add_provider(carthage.network.BridgeNetwork,
                                        carthage.network.BridgeNetwork(self.bridge_name, delete_bridge = False))
            
__all__ += ['NetworkModel']

class NetworkConfigModelType(InjectableModelType):

    @modelmethod
    def add(cls, ns, interface, net, mac):
        def callback(inst):
            inst.add(interface, net, mac)
        cls._add_callback(ns, callback)

class NetworkConfigModel(InjectableModel,
                         carthage.network.NetworkConfig,
                         metaclass = NetworkConfigModelType
                         ):
    pass


__all__ += ['NetworkConfigModel']

class ModelGroup(InjectableModel, metaclass = ModelingContainer): pass

class Enclave(InjectableModel, metaclass = ModelingContainer):

    domain: str

    @classmethod
    def our_key(self):
        return InjectionKey(Enclave, domain=self.domain)

__all__ += ['ModelGroup', 'Enclave']

class MachineModelType(ModelingContainer):

    def __new__(cls, name, bases, ns, **kwargs):
        if 'name' not in ns:
            ns['name'] = name.lower()
        if '.' not in ns['name']:
            try:
                ns['name'] = ns['name'] + '.' + ns['domain']
            except KeyError: pass
        return super().__new__(cls, name, bases, ns, **kwargs)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not kwargs.get('template', False):
            self.__globally_unique_key__ = self.our_key()

    @modelmethod
    def add_ansible_role(self, *args):
        pass #This is a stub

        
class MachineModel(InjectableModel, metaclass = MachineModelType, template = True):

    @classmethod
    def our_key(cls):
        return InjectionKey(MachineModel, host = cls.name)

    network_config = injector_access(InjectionKey(carthage.network.NetworkConfig))

    
__all__ += ['MachineModel']

