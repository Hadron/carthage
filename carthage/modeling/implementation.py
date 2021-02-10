# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import functools, threading, typing
from carthage.dependency_injection import * # type: ignore
from .utils import *
from carthage.network import NetworkConfig
# There is a circular import of decorators at the end.

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

    to_inject: typing.Dict[InjectionKey, typing.Tuple[typing.Any, dict]]

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
        super().__init__()
        for k,v in initial.items():
            if isinstance(v, modelmethod):
                v = functools.partial(v.method, cls, self)
            super().__setitem__(k, v)
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

    def setitem(self, k, v, explicit: bool = True):
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

class modelmethod:

    '''Usage within the body of a :class:`ModelingBase` subclass::

        @modelmethod
def method(cls, ns, *args, **kwargs):

    Will add ``meth`` to any class using the class where this modelmethod is added as a metaclass.

    :param cls: The metaclass in use

    :param ns: The namespace of the class being defined

    Remaining parameters are passed from the call to modelmethod.

    '''
    def __init__(self, meth):
        self.method = meth

    def __repr__(self):
        return f'<modelmethod: {repr(self.method)}>'
    
__all__ += ['modelmethod']

class ModelingBase(type):

    def _handle_decorator(target_cls, ns, k, state):
        if isinstance(state.value, decorators.ModelingDecoratorWrapper):
            return state.value.handle(target_cls, ns, k, state)
            
    namespace_filters: typing.List[typing.Callable] = [_handle_decorator]
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
        if 'namespace_filters' not in cls.__dict__:
            cls.namespace_filters = []
        if 'namespace_initial' not in cls.__dict__:
            cls.namespace_initial = {}
        for k,v in cls.__dict__.items():
            if isinstance(v, modelmethod):
                cls.namespace_initial[k] = v # Parsed further in the namespace constructor
                

__all__ += ["ModelingBase"]


class InjectableModelType(ModelingBase):

    classes_to_inject: typing.Sequence[type] = (NetworkConfig, )
    _callbacks: typing.List[typing.Callable]


    def _handle_provides(target_cls, ns, k, state):
        if hasattr(state.value, '__provides_dependencies_for__'):
            state.extra_keys.extend(state.value.__provides_dependencies_for__)

    namespace_filters = [_handle_provides]

    @classmethod
    def _add_callback(cls, ns:ModelingNamespace, cb: typing.Callable):
        ns.setdefault('_callbacks', [])
        ns['_callbacks'].append(cb)
        
    def __new__(cls, name, bases, namespace, **kwargs):
        to_inject = namespace.to_inject
        namespace.setdefault('_callbacks', [])
        self = super(InjectableModelType,cls).__new__(cls, name, bases, namespace, **kwargs)
        self.__initial_injections__ = to_inject
        return self
    

    def __init_subclass__(cls, *args, **kwargs):
        if 'classes_to_inject' not in cls.__dict__:
            cls.classes_to_inject = []
        super().__init_subclass__(*args, **kwargs)

    @modelmethod
    def add_provider(cls, ns, k:InjectionKey,
                     v: typing.Any,
                     close = True,
                     allow_multiple = False):
        def callback(inst):
            inst.injector.add_provider(
                k, v,
                close = close, allow_multiple = allow_multiple)
        cls._add_callback(ns, callback)
        
__all__ += ['InjectableModelType']

        
class ModelingContainer(InjectableModelType):

#: Returns the key under which we are registered in the parent.  Our
#objects will be adapted by adding these constraints to register in
#the parent as well.

    
    
    def _integrate_containment(target_cls, ns, k, state):
        def propagate_provider(k_inner, v, options):
            #There is the provides_dependencies_for (or outer) keys, and there is a
            #set of inner keys from the providers being injected.  We
            #want to pick one outer key and also add the inner keys
            #(plus the constraints from the outer key) into the outer
            #injector.  We pick one outer key just for simplicity; it
            #could be made to work with more.
            outer_constraints = outer_key.constraints
            if set(outer_constraints) & set(k_inner.constraints):
                return
            k_new = InjectionKey(k_inner.target, **outer_constraints, **k_inner.constraints)
            ns.to_inject[k_new] = (v, options)
            
        if not isinstance(state.value, ModelingContainer): return
        val = state.value
        if hasattr(val, '__provides_dependencies_for__'):
            outer_key = None
            for outer_key in val.__provides_dependencies_for__:
                if isinstance(outer_key.target, ModelingContainer) and len(outer_key.constraints) > 0:
                    break
            if outer_key is None or len(outer_key.constraints) == 0: return
            for k, info in val.__initial_injections__.items():
                if not isinstance(k.target, type): continue
                v, options = info
                propagate_provider(k, v, options)

    namespace_filters = [_integrate_containment]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            our_key  = self.our_key()
            if not isinstance(our_key, InjectionKey):
                our_key = InjectionKey(our_key)
            setattr_default(self, '__provides_dependencies_for__', [])
            self.__provides_dependencies_for__.insert(0,our_key)
        except (AttributeError, NameError): pass
        
__all__ += ['ModelingContainer']

from . import decorators
