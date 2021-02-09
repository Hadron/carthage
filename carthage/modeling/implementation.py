# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.dependency_injection import * # type: ignore
import typing
from .utils import *
from carthage.network import NetworkConfig

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

    def __init__(self, filters: typing.List[typing.Callable],
                 initial: typing.Mapping,
                 classes_to_inject: typing.Sequence[type]):
        self.filters = filters
        self.classes_to_inject = frozenset(classes_to_inject)
        self.to_inject = {}
        super().__init__(initial)
        self.initially_set = set(self.keys())

    def __setitem__(self, k, v):
        state = InjectionEntry(v)
        if isinstance(v, type) and (self.classes_to_inject & set(v.__bases__)):
            state.inject_by_class = True
        handled = False
        for f in self.filters:
            if f(self, k, state):
                #The filter has handled things
                handled = True
        else:
            if not handled: super().__setitem__(k,state.value)
            try: self.initially_set.remove(k)
            except KeyError: pass
        if k.startswith('_'):
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
        return ModelingNamespace(filters = namespace_filters,
                                 initial = initial,
                                 classes_to_inject = classes_to_inject)

    def __new__(cls, name, bases, namespace, **kwargs):
        for k in namespace.initially_set:
            try: del namespace[k]
            except Exception: pass
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
