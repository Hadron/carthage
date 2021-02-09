import threading, typing
from carthage.dependency_injection import * # type: ignore
from .utils import *
from carthage.network import NetworkConfig

thread_local = threading.local()

__all__ = []

class InjectionEntry:

    __slots__ = ['extra_keys', 'inject_by_name',
                 'inject_by_class', 'value',
                 'perform_close', 'allow_multiple']
    extra_keys: list
    inject_by_name: bool
    inject_by_class: bool
    perform_close: bool
    allow_multiple: bool
    

    def __init__(self, value):
        self.value = value
        self.inject_by_name = True
        self.inject_by_class = False
        self.extra_keys = []
        self.perform_close = True
        self.allow_multiple = False
        

    @property
    def injection_options(self):
        return dict(
            allow_multiple = self.allow_multiple,
            close = self.perform_close)
    
    def __repr__(self):
        return f'<InjectionEntry: name = {self.inject_by_name}, class = {self.inject_by_class}, keys = {self.extra_keys}>'

    

class ModelingNamespace(dict):

    '''A dict used as the class namespace for modeling objects.  Allows overrides for:

    * filters to change the value or name that an item is injected under

    * Handling managing inejectionkeys

    '''

    def __init__(self, cls: type,
                 filters: typing.List[typing.Callable],
                 initial: typing.Mapping,
                 classes_to_inject: typing.Sequence[type]):
        if not hasattr(thread_local, 'current_context'):
            thread_local.current_context = None
        self.cls = cls
        self.filters = filters
        self.classes_to_inject = frozenset(classes_to_inject)
        self.to_inject = {}
        super().__init__(initial)
        self.parent_context = thread_local.current_context
        self.context_imported = False
        self.initially_set = set(self.keys())

    def __setitem__(self, k, v):
        if thread_local.current_context is not self:
            self.update_context()
        state = InjectionEntry(v)
        if isinstance(v, type) and (self.classes_to_inject & set(v.__bases__)):
            state.inject_by_class = True
        handled = False
        for f in self.filters:
            if f(self.cls, self, k, state):
                #The filter has handled things
                handled = True
        else:
            if not handled: super().__setitem__(k,state.value)
            try: self.initially_set.remove(k)
            except KeyError: pass
        if k.startswith('_'):
            if k == "__qualname__": self.import_context()
            return state.value
        if state.inject_by_name:
            self.to_inject[InjectionKey(k)] = (state.value, state.injection_options)
        if state.inject_by_class and isinstance(state.value, type):
            for b in state.value.__bases__:
                if b in self.classes_to_inject:
                    self.to_inject[InjectionKey(b)] = (state.value, state.injection_options)
        for k in state.extra_keys:
            self.to_inject[k] = (state.value, state.injection_options)
        return state.value

    def setitem(k, v, explicit: bool = True):
        '''An override for use in filters to avoid recursive filtering'''
        res = super().__setitem__(k, v)
        if explicit:
            try: self.initially_set.remove(k)
            except KeyError: pass
        return res

    def import_context(self):
        # We only want to import the context if our qualname is an
        # extension of their qualname, so we need to wait until the
        # first time our qualname is set.  If our qualname is later
        # adjusted we do not want to reimport.
        if (self.parent_context is None) or self.context_imported: return
        our_qualname = self['__qualname__']
        parent = self.parent_context
        parent_qualname = parent['__qualname__']
        our_qualname_count = len(our_qualname.split("."))
        parent_qualname_count = len(parent_qualname.split("."))
        self.context_imported = True
        if not (our_qualname.startswith(parent_qualname) and our_qualname_count == parent_qualname_count+1):
            return
        for k in parent.keys():
            if k in self: continue
            super().__setitem__(k, parent[k])
            self.initially_set.add(k)

    def update_context(self):
        thread_local.current_context = self
        

class ModelingBase(type):

    namespace_filters: typing.List[typing.Callable] = []
    namespace_initial: typing.Mapping = {}
    
    @classmethod
    def __prepare__(cls, *args, **kwargs):
        classes_to_inject = combine_mro_list(cls, InjectableModelType, 'classes_to_inject')
        namespace_filters = combine_mro_list(cls, ModelingBase,
                                             'namespace_filters')
        initial = combine_mro_mapping(cls,
                                      ModelingBase,
                                      'namespace_initial')
        return ModelingNamespace(cls, filters = namespace_filters,
                                 initial = initial,
                                 classes_to_inject = classes_to_inject)

    def __new__(cls, name, bases, namespace, **kwargs):
        for k in namespace.initially_set:
            try: del namespace[k]
            except Exception: pass
        thread_local.current_context = None
        return super(ModelingBase, cls).__new__(cls, name, bases, namespace, **kwargs)

    def __init_subclass__(cls, *args):
        cls.namespace_filters = []
        cls.namespace_initial = {}


__all__ += ["ModelingBase"]


class InjectableModelType(ModelingBase):

    classes_to_inject: typing.Sequence[type] = (NetworkConfig, )
    
    def __new__(cls, name, bases, namespace, **kwargs):
        to_inject = namespace.to_inject
        self = super(InjectableModelType,cls).__new__(cls, name, bases, namespace, **kwargs)
        self.__initial_injections__ = to_inject
        return self
    

    def __init_subclass__(cls, *args, **kwargs):
        if 'classes_to_inject' not in cls.__dict__:
            cls.classes_to_inject = []
        super().__init_subclass__(*args, **kwargs)

        
__all__ += ['InjectableModelType']

class ModelingDecoratorWrapper:

    subclass: type = ModelingBase
    name: str

    def __init__(value):
        self.value = value

    def __repr__(self):
        return f'{self.__class__.name}({repr(self.value)})'

    def handle(self, cls, ns, k, state):
        if not issubclass(cls, self.subclass):
            raise TypeError(f'{self.__class__.name} decorator only for subclasses of {self.cls.__name__}')
        state.value =  self.value

class InjectAsDecorator(ModelingDecoratorWrapper):
    subclass = InjectableModelType
    name = "inject_as"

    def __init__(self, value, *keys):
        super().__init__(value)
        self.keys = keys

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.extra_keys.extend(self.keys)

def inject_as(*keys):
    def wrapper(val):
        return InjectAsDecorator(val, *keys)
    return wrapper

        
class ModelingContainer(InjectableModelType):

#: Returns the key under which we are registered in the parent.  Our
#objects will be adapted by adding these constraints to register in
#the parent as well.
    our_key: InjectionKey
    
