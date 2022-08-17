# Copyright (C) 2021, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import enum, functools, inspect, threading, typing, warnings
from carthage.dependency_injection import *
from carthage.dependency_injection.base import default_injection_key # not part of public api
from carthage.dependency_injection.base import InjectorXrefMarker
from .utils import *
from carthage.network import NetworkConfig
import carthage.machine
# There is a circular import of decorators at the end.

thread_local = threading.local()

__all__ = []

class NSFlags(enum.Flag):
    close = 1
    allow_multiple = 2
    inject_by_name = 4
    inject_by_class = 8
    instantiate_on_access = 16
    propagate_key = 32
    dependency_quote = 64

classes_to_quote = set()

class NsEntry:

    __slots__ = ['extra_keys',                  'value',
                 'flags', 'new_name', 'transclusion_key']
    flags: NSFlags
    extra_keys: list
    new_name: typing.Optional[str]
    transclusion_key: typing.Optional[InjectionKey]


    def __init__(self, value):
        self.value = value
        self.extra_keys = []
        self.flags = NSFlags.close | NSFlags.instantiate_on_access | NSFlags.inject_by_name|NSFlags.inject_by_class
        if isinstance(value, type) and (classes_to_quote  & set(value.__mro__)):
            self.flags |= NSFlags.dependency_quote
        self.new_name = None
        self.transclusion_key = None


    @property
    def injection_options(self):
        f = self.flags
        d =  dict(
            allow_multiple = bool(f&NSFlags.allow_multiple),
            close = bool(f&NSFlags.close),
            )
        return d

    def __repr__(self):
        return f'<NsEntry: flags = {self.flags}, keys: {self.extra_keys}, value: {self.value}>'


    def instantiate_value(self, name):
        if (self.flags&NSFlags.instantiate_on_access) and \
           (self.transclusion_key or  isinstance(self.value, type)):
            if self.transclusion_key:
                key = self.transclusion_key
            elif self.flags&NSFlags.inject_by_name:
                key = InjectionKey(name)
            elif self.extra_keys:
                key = self.extra_keys[0]
            else: return self.value
            return decorators.injector_access(key, self.value)
        return self.value





class ModelingNamespace(dict):

    '''A dict used as the class namespace for modeling objects.  Allows overrides for:

    * filters to change the value or name that an item is injected under

    * Handling managing inejectionkeys

    '''

    to_inject: typing.Dict[InjectionKey, typing.Tuple[typing.Any, dict]]
    #: The set of keys that may be transcluded introduced in this
    #namespace.  Notice that __tranclusions__ in the resulting class
    #has a slightly different format.
    transclusions: typing.Set[typing.Tuple[InjectionKey, InjectionKey]]
    initial: typing.Dict[str, typing.Any]

    def __init__(self, cls: type,
                 filters: typing.List[typing.Callable],
                 initial: typing.Mapping,
                 classes_to_inject: typing.Sequence[type]):
        if not hasattr(thread_local, 'current_context'):
            thread_local.current_context = None
        self.cls = cls
        self.filters = list(reversed(filters))
        self.classes_to_inject = frozenset(classes_to_inject)
        self.to_inject = {}
        self.transclusions = set()
        super().__init__()
        self.initial = {}
        for k,v in initial.items():
            if isinstance(v, modelmethod):
                v = functools.partial(v.method, cls, self)
            self.initial[k] = v
        self.parent_context = thread_local.current_context
        self.context_imported = False
        self.setitem('__transclusions__', set())

    def keys_for(self, name, state):
        # returns key, (value, options)
        def val(k):
            if k == state.transclusion_key or state.transclusion_key is None:
                value = state.value
                if state.flags & NSFlags.dependency_quote:
                    return dependency_quote(value)
                else: return value
            return decorators.injector_access(state.transclusion_key)
        options = state.injection_options
        if state.flags & NSFlags.inject_by_name:
            yield InjectionKey(name), (val(InjectionKey(name)), options)
        if isinstance(state.value, type) and (state.flags & NSFlags.inject_by_class):
            for b in state.value.__mro__:
                if b in self.classes_to_inject:
                    try: class_key = state.value.default_class_injection_key()
                    except AttributeError:
                        class_key = InjectionKey(b)
                    class_key = InjectionKey(b, **class_key.constraints)
                    yield class_key, (val(class_key), options)
        try:
            global_key = state.value.__globally_unique_key__
            global_options = state.injection_options
            global_options['globally_unique'] = True
            yield global_key, (val(global_key), global_options)
        except (AttributeError, KeyError): global_key = None
        for k in state.extra_keys:
            if global_key and (global_key ==k): continue
            yield k, (val(k), options)

    def __getitem__(self, k):
        # Normally when we set an item we drop it from initial
        # but there are some cases where we want a value for getitem different than the value that ends up in the eventual class
        # the obvious case is injector_access from the instantiate flag.
        # so initial actually takes precidence over our actual values.
        try: return self.initial[k]
        except KeyError: return super().__getitem__(k)
        


    def __contains__(self,k):
        return super().__contains__(k) or k in self.initial

    def __delitem__(self,k):
        del self[k]
        try: del self.to_inject[k]
        except: pass
        
    def __setitem__(self, k, v):
        if thread_local.current_context is not self:
            self.update_context()
        state = NsEntry(v)
        handled = False
        for f in self.filters:
            if f(self.cls, self, k, state):
                #The filter has handled things
                handled = True
            if state.new_name:
                k = state.new_name
                state.new_name = None
        else:
            if not handled:
                iv = state.instantiate_value(k)
                super().__setitem__(k,iv)
                if iv is not state.value:
                    self.initial[k] = state.value
                else:
                    try: del self.initial[k]
                    except KeyError: pass
        if handled:
            try: del self.initial[k]
            except KeyError: pass
        if k.startswith('_'):
            if k == "__qualname__": self.import_context()
            return state.value
        for k, info in self.keys_for(name = k, state = state):
            self.to_inject[k] = info
        return state.value

    def __delitem__(self, k):
        res = super().__delitem__(k)
        try: del self.to_inject[InjectionKey(k)]
        except Exception: pass
        return res

    def setitem(self, k, v, explicit: bool = True):
        '''An override for use in filters to avoid recursive filtering'''
        res = super().__setitem__(k, v)
        if explicit:
            try: del self.initial[k]
            except KeyError: pass
        return res

    def keys(self):
        yield from super().keys()
        for k in self.initial.keys():
            if not  super().__contains__(k): yield k

    def get(self, k, default):
        try: return self.initial[k]
        except KeyError:
            return super().get(k, default)
        
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
            self.parent_context = None #Also break injection key lookups at this point
            return
        for k in parent.keys():
            if k in self: continue
            if k.startswith('_'): continue
            self.initial[k] = parent[k]

    def update_context(self):
        thread_local.current_context = self

    def get_injected(self, key):
        parent = self
        while parent is not None:
            if key in parent.to_inject:
                res =  parent.to_inject[key][0]
                if isinstance(res, dependency_quote): res = res.value
                return res
            parent = parent.parent_context
        raise KeyError

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
        namespace.initial.clear()
        thread_local.current_context = None
        try: return super(ModelingBase, cls).__new__(cls, name, bases, namespace, **kwargs)
        except TypeError as e:
            raise TypeError(f'Error constructing ${name}: {str(e)}') from None

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
            if isinstance(state.value, InjectableModelType):
                ns.setdefault('__transclusions__', set())
                ns['__transclusions__'] |= state.value.__transclusions__

    namespace_filters = [_handle_provides]

    @classmethod
    def _add_callback(cls, ns:ModelingNamespace, cb: typing.Callable):
        ns.setdefault('_callbacks', [])
        ns['_callbacks'].append(cb)

    def __new__(cls, name, bases, namespace, **kwargs):
        to_inject = namespace.to_inject
        namespace.setdefault('_callbacks', [])
        transclusions_initial = set()
        for b in bases:
            if isinstance(b, InjectableModelType):
                transclusions_initial |= b.__transclusions__
        if '__transclusions__' in namespace: transclusions_initial |= namespace['__transclusions__']
        self = super(InjectableModelType,cls).__new__(cls, name, bases,namespace,  **kwargs)
        for ko, ki in namespace.transclusions:
            transclusions_initial.add((ko, ki, self))
        self.__transclusions__ = transclusions_initial
        initial_injections = dict()
        for c in bases:
            if hasattr(c, '__initial_injections__'):
                initial_injections.update(c.__initial_injections__)
        initial_injections.update(to_inject)
        self.__initial_injections__ = initial_injections
        return self


    def __init_subclass__(cls, *args, **kwargs):
        if 'classes_to_inject' not in cls.__dict__:
            cls.classes_to_inject = []
        super().__init_subclass__(*args, **kwargs)

    @modelmethod
    def add_provider(cls, ns, k:InjectionKey,
                     v: typing.Any = None,
                     close = True,
                     allow_multiple = False, globally_unique = False,
                     propagate = False,
                     transclusion_overrides = False):
        if not isinstance(k, InjectionKey) and v is None:
            v = k
            k = default_injection_key(k)
        if transclusion_overrides:
            ns.transclusions.add((k,k))
        ns.to_inject[k] = (v, dict(
            close = close,
            allow_multiple = allow_multiple,
            globally_unique = globally_unique,
            ))
        if propagate:
            assert issubclass(cls, ModelingContainer), "Only ModelingContainers accept propagation"
            ns.to_propagate[k] = ns.to_inject[k]

    @modelmethod
    def disable_system_dependency(cls, ns, dependency):
        ns.to_inject[dependency.default_instance_injection_key()] = (
            None, dict(close = False, allow_multiple = False))

    @modelmethod
    def self_provider(cls, ns, k: InjectionKey):
        def callback(inst):
            inst.injector.add_provider(k, dependency_quote(inst))
        cls._add_callback(ns, callback)


__all__ += ['InjectableModelType']

def handle_transclusions(val, injector):
    try:
        transclusions = val.__transclusions__
    except AttributeError: return
    for ko, ki, o in transclusions:
        target = injector.injector_containing(ko)
        if target:
            try: del o.__initial_injections__[ki]
            except KeyError: pass
    del val.__transclusions__
    return val


class ModelingContainer(InjectableModelType):

#: Returns the key under which we are registered in the parent.  Our
#objects will be adapted by adding these constraints to register in
#the parent as well.
    our_key: typing.Callable[[object], InjectionKey]


    def _integrate_containment(target_cls, ns, k, state):
        def propagate_provider(k_inner, v, options):
            #There is the provides_dependencies_for (or outer) keys, and there is a
            #set of inner keys from the providers being injected.  We
            #want to pick one outer key and also add the inner keys
            #(plus the constraints from the outer key) into the outer
            #injector.  We pick one outer key just for simplicity; it
            #could be made to work with more.
            globally_unique = options.get('globally_unique', False)
            inner_transcludable = (k_inner, k_inner, val) in val.__transclusions__
            if not globally_unique:
                outer_constraints = outer_key.constraints
                if set(outer_constraints) & set(k_inner.constraints):
                    return
                k_new = InjectionKey(k_inner.target, **outer_constraints, **k_inner.constraints)
            else:
                k_new = k_inner #globally unique
            # Must be after we have chosen a globally unique key if there is one.
            if isinstance(v, decorators.injector_access):
                # Normally injector_access will not actually get a key
                # in __initial_injections__, but there are important
                # cases where that happens, like the machine entry on
                # MachineModel.  In general this situation will come
                # up if some modeling type wishes to propagate
                # something that is referenced by an InjectionKey
                # rather than a value.  One of the biggest reasons to
                # try and collapse out injector_access is that
                # injector_access cannot deal with AsyncRequired but
                # injector_xref can.
                k_inner = v.key
            # In some cases we could make the injector_xref more clear
            # by adding the outer key's constraints to
            # v.injectable_key and use that as the new injectable key,
            # preserving the inner target key.  That generally works
            # so long as the injectable_key is not masked by a key
            # already in the outer injector or by an overlapping
            # constraint.  Also, It just moves the recursion around.
            v = injector_xref(outer_key, k_inner)
            if k_new not in ns.to_inject:
                ns.to_inject[k_new] = (v, options)
                ns.to_propagate[k_new] = (v, options)
                if inner_transcludable:
                    ns.transclusions.add((k_new, k_new))
                if (outer_key,outer_key) in ns.transclusions:
                    ns.transclusions.add((k_new, k_new))
                    ns.transclusions.add((outer_key, k_new))

        if not isinstance(state.value, ModelingContainer): return
        val = state.value
        outer_key = None
        if hasattr(val, '__provides_dependencies_for__'):
            for outer_key in val.__provides_dependencies_for__:
                if isinstance(outer_key.target, ModelingContainer) and len(outer_key.constraints) > 0:
                    break
        if outer_key is None or len(outer_key.constraints) == 0:
            warnings.warn("Cannot propagate because no outer key with constraints found", stacklevel=3)
            return
        to_propagate = combine_mro_mapping(val, ModelingContainer, '__container_propagations__')
        to_propagate.update(val.__container_propagations__)
        for k, info in to_propagate.items():
                v, options = info
                propagate_provider(k, v, options)

    def _propagate_filter(target_cls, ns, k, state):
        if isinstance(state.value, ModelingContainer) or (state.flags&NSFlags.propagate_key):
            for k, info in ns.keys_for(name = k, state = state):
                ns.to_propagate[k] = info

    namespace_filters = [_integrate_containment, _propagate_filter]

    @classmethod
    def __prepare__(cls, *args, **kwargs):
        ns = super().__prepare__(*args, **kwargs)
        ns.to_propagate = {}
        return ns

    def __new__(cls, name, bases, ns, **kwargs):
        to_propagate = ns.to_propagate
        self = super().__new__(cls, name, bases, ns, **kwargs)
        for k in list(to_propagate.keys()):
            if k not in self.__initial_injections__:
                del to_propagate[k]
        self.__container_propagations__ = to_propagate
        return self

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            our_key  = self.our_key()
            if not isinstance(our_key, InjectionKey):
                our_key = InjectionKey(our_key)
            setattr_default(self, '__provides_dependencies_for__', [])
            self.__provides_dependencies_for__.insert(0,our_key)
        except (AttributeError, NameError): pass

    @modelmethod
    def include_container(cls, ns, obj,
                          *, close = True,
                          allow_multiple = False,
                          dynamic_name = None,
                          **kwargs):
        def handle_function(func):
            params = set(inspect.signature(func).parameters.keys())
            open_params = params - set(kwargs.keys())
            for k in open_params:
                if k not in ns:
                    raise AttributeError( f'{k} not found in enclosing class')
                kwargs[k] = ns[k]
            return func(**kwargs)
        if not hasattr(obj, '__provides_dependencies_for__') and callable(obj):
            obj = handle_function(obj)
        if (not hasattr(obj, '__provides_dependencies_for__')) and not dynamic_name:
            raise TypeError( f'{obj} is not a modeling container class')
        state = NsEntry(obj)
        if dynamic_name:
            # This is gross, but it's not clear how to support
            # decorators ourselves
            if (not close) or allow_multiple:
                raise TypeError('If using dynamic_name, use decorators to adjust close and allow_multple')
            ns[fixup_dynamic_name(dynamic_name)] = obj
            return
        ModelingContainer._integrate_containment(cls, ns, obj.__name__, state)
        # Since we're not able to use ns.__setitem__, do the injection key stuff ourself.
        if not close:
            state.flags &= ~NSFlags.close
        if allow_multiple:
            state.flags |= NSFlags.allow_multiple
        options = state.injection_options
        for k in obj.__provides_dependencies_for__:
            options['globally_unique'] = (k == getattr(obj, '__globally_unique_key__', None))
            if k not in ns.to_inject:
                ns.to_inject[k] = (obj, options)
                ns.to_propagate[k] = (obj, options)


__all__ += ['ModelingContainer']

def adjust_bases_for_tasks(bases: tuple[type], namespace: dict) -> tuple[type]:
    '''
If the namespace includes any setup_tasks, then add SetupTaskMixin to the baseses.
    '''
    from ..setup_tasks import SetupTaskMixin, TaskWrapper
    if SetupTaskMixin in bases: return bases
    for v in namespace.values():
        if isinstance(v, TaskWrapper): break
    else:
        return bases
    new_bases = [*bases, SetupTaskMixin]
    for c in new_bases:
        if AsyncInjectable in c.__mro__: break
    else:
        new_bases.append(AsyncInjectable)
    return tuple(new_bases)


__all__ += ['adjust_bases_for_tasks']

def _handle_base_customization(target_cls, ns, k, state):
    val = state.value
    if not state.flags & NSFlags.instantiate_on_access: return
    if not isinstance(val, type): return
    if not issubclass(val,carthage.machine.BaseCustomization): return
    if state.flags & NSFlags.inject_by_class:
        key = val.default_class_injection_key()
        key = InjectionKey(carthage.machine.BaseCustomization, **key.constraints)
        ns.to_inject[key] = dependency_quote(val), state.injection_options
    state.flags &= ~(NSFlags.inject_by_class | NSFlags.instantiate_on_access)
    state.value = decorators.wrap_base_customization(val, state.new_name or k)

__all__ += ['_handle_base_customization']

from . import decorators
