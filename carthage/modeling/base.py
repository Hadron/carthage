from .implementation import *
from carthage.dependency_injection import * #type: ignore
import typing
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
        for k,info in self.__class__.__initial_injections__.items():
            v, options = info
            try:
                self.injector.add_provider(k, v, **options)
            except Exception as e:
                raise RuntimeError(f'Failed registering {v} as provider for {k}') from e

        for cb in self.__class__._callbacks:
            cb(self)
            
__all__ += ['InjectableModel']

class Enclave(InjectableModel, metaclass = ModelingContainer):

    domain: str

    @classmethod
    def our_key(self):
        return InjectionKey(Enclave, domain=self.domain)

__all__ += ['Enclave']

