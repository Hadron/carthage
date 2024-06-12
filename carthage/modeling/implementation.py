# Copyright (C) 2021, 2022, 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import enum
import functools
import inspect
import threading
import typing
import warnings
from carthage.dependency_injection import *
from carthage.dependency_injection.base import default_injection_key  # not part of public api
from carthage.dependency_injection.base import InjectorXrefMarker
from .utils import *
from carthage.network import NetworkConfig
import carthage.machine
import carthage.console
# There is a circular import of decorators at the end.

thread_local = threading.local()

__all__ = []


class NSFlags(enum.Flag):
    close = 1
    allow_multiple = 2
    inject_by_name = 4
    inject_by_class = 8
    instantiate_on_access = 16
    # 32 previously used by propagate_key, but that has been reworked
    dependency_quote = 64


classes_to_quote = set()


class NsEntry:

    __slots__ = ['extra_keys', 'value',
                 'flags', 'new_name', 'transclusion_key']
    flags: NSFlags
    extra_keys: list
    new_name: typing.Optional[str]
    transclusion_key: typing.Optional[InjectionKey]

    def __init__(self, value):
        self.value = value
        self.extra_keys = []
        self.flags = NSFlags.close | NSFlags.instantiate_on_access | NSFlags.inject_by_name | NSFlags.inject_by_class
        if isinstance(value, type) and (classes_to_quote & set(value.__mro__)):
            self.flags |= NSFlags.dependency_quote
        self.new_name = None
        self.transclusion_key = None

    @property
    def injection_options(self):
        f = self.flags
        d = dict(
            allow_multiple=bool(f & NSFlags.allow_multiple),
            close=bool(f & NSFlags.close),
        )
        return d

    def __repr__(self):
        return f'<NsEntry: flags = {self.flags}, keys: {self.extra_keys}, value: {self.value}>'

    def instantiate_value(self, name):
        if (self.flags & NSFlags.instantiate_on_access) and \
           (self.transclusion_key or isinstance(self.value, type)):
            if self.transclusion_key:
                key = self.transclusion_key
            elif self.extra_keys:
                key = self.extra_keys[0]
            elif self.flags & NSFlags.inject_by_name:
                key = InjectionKey(name)
            else:
                return self.value
            return decorators.injector_access(key, self.value)
        return self.value


class ModelingNamespace(dict):

    '''A dict used as the class namespace for modeling objects.  Allows overrides for:

    * filters to change the value or name that an item is injected under

    * Handling managing InjectionKeys

    '''

    to_inject: typing.Dict[InjectionKey, typing.Tuple[typing.Any, dict]]
    #: A mapping from keys that can be transcluded in this injector to a set of keys in self.to_inject that should not be provided by the injector in an :class:`InjectableModelType` if the mapped key is transcluded.
    transclusions: typing.Mapping[InjectionKey, typing.Set[InjectionKey]]
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
        self.transclusions = dict()
        super().__init__()
        self.initial = {}
        for k, v in initial.items():
            if k.startswith('_'):
                self.setitem(k,v)
            elif isinstance(v, modelmethod):
                v = functools.partial(v,  cls, self)
            self.initial[k] = v
        self.parent_context = thread_local.current_context
        self.context_imported = False
        self.to_inject = dict()



    def __getitem__(self, k):
        # Normally when we set an item we drop it from initial
        # but there are some cases where we want a value for getitem different than the value that ends up in the eventual class
        # the obvious case is injector_access from the instantiate flag.
        # so initial actually takes precidence over our actual values.
        try:
            return self.initial[k]
        except KeyError:
            return super().__getitem__(k)

    def __contains__(self, k):
        return super().__contains__(k) or k in self.initial

    def __delitem__(self, k):
        del self[k]
        try:
            del self.to_inject[k]
        except BaseException:
            pass

    def __setitem__(self, k, v):
        if thread_local.current_context is not self:
            self.update_context()
        state = NsEntry(v)
        handled = False
        for f in self.filters:
            if f(self.cls, self, k, state):
                # The filter has handled things
                handled = True
            if state.new_name:
                k = state.new_name
                state.new_name = None
        else:
            if not handled:
                iv = state.instantiate_value(k)
                super().__setitem__(k, iv)
                if iv is not state.value:
                    self.initial[k] = state.value
                else:
                    try:
                        del self.initial[k]
                    except KeyError:
                        pass
        if handled:
            try:
                del self.initial[k]
            except KeyError:
                pass
        if k.startswith('_'):
            if k == "__qualname__":
                self.import_context()
            return state.value
        transclusion_keys = None
        if state.transclusion_key: transclusion_keys = {state.transclusion_key}
        for k, info in keys_for(name=k, state=state, classes_to_inject=self.classes_to_inject):
            self.to_inject[k] = info
            if transclusion_keys: transclusion_keys.add(k)
        if transclusion_keys:
            self.transclusions[state.transclusion_key] = frozenset(transclusion_keys)
        return state.value

    def __delitem__(self, k):
        res = super().__delitem__(k)
        try:
            del self.to_inject[InjectionKey(k)]
        except Exception:
            pass
        return res

    def setitem(self, k, v, explicit: bool = True):
        '''An override for use in filters to avoid recursive filtering'''
        res = super().__setitem__(k, v)
        if explicit:
            try:
                del self.initial[k]
            except KeyError:
                pass
        return res

    def keys(self):
        yield from super().keys()
        for k in self.initial.keys():
            if not super().__contains__(k):
                yield k

    def get(self, k, default):
        try:
            return self.initial[k]
        except KeyError:
            return super().get(k, default)

    def import_context(self):
        # We only want to import the context if our qualname is an
        # extension of their qualname, so we need to wait until the
        # first time our qualname is set.  If our qualname is later
        # adjusted we do not want to reimport.
        if (self.parent_context is None) or self.context_imported:
            return
        our_qualname = self['__qualname__']
        parent = self.parent_context
        parent_qualname = parent['__qualname__']
        our_qualname_count = len(our_qualname.split("."))
        parent_qualname_count = len(parent_qualname.split("."))
        self.context_imported = True
        if not (our_qualname.startswith(parent_qualname) and our_qualname_count == parent_qualname_count + 1):
            self.parent_context = None  # Also break injection key lookups at this point
            return
        for k in parent.keys():
            if k in self:
                continue
            if k.startswith('_'):
                continue
            self.initial[k] = parent[k]

    def update_context(self):
        thread_local.current_context = self

    def get_injected(self, key):
        parent = self
        while parent is not None:
            if key in parent.to_inject:
                res = parent.to_inject[key][0]
                if isinstance(res, dependency_quote):
                    res = res.value
                return res
            parent = parent.parent_context
        raise KeyError

def keys_for(name, state, classes_to_inject):
    '''
    Returns the set of keys for a given state item.
    '''
    # returns key, (value, options)
    def val(k):
        value = state.value
        if state.flags & NSFlags.dependency_quote:
            return dependency_quote(value)
        else:
            return value

    options = state.injection_options
    state.extra_keys.sort(key=lambda k:k.globally_unique, reverse=True)
    if state.flags & NSFlags.inject_by_name:
        name_key = InjectionKey(name)
        # If name_key is in extra_keys, use that, so we avoid
        # differing in _globally_unique or _ready
        if name_key not in state.extra_keys:
            yield InjectionKey(name), (val(InjectionKey(name)), options)
    if isinstance(state.value, type) and (state.flags & NSFlags.inject_by_class):
        for b in state.value.__mro__:
            if b in classes_to_inject:
                try:
                    class_key = state.value.default_class_injection_key()
                except AttributeError:
                    class_key = InjectionKey(b)
                class_key = InjectionKey(b, **class_key.constraints)
                yield class_key, (val(class_key), options)
    for k in state.extra_keys:
        yield k, (val(k), options)

def check_already_provided(metaclass_proxy, v):
    if not hasattr(metaclass_proxy, '__namespace__'):
        return None
    namespace = metaclass_proxy.__namespace__
    for k in getattr(v, '__provides_dependencies_for__', []):
        try:
            namespace.get_injected(k)
            return k
        except KeyError: continue
    return None

class ModelingNamespaceProxy:


    local_attributes = frozenset({
        '_metaclass',
        '_namespace',
        '_add_callback',
        '__namespace__',
        '__metaclass__',
        '__class__',
                })

    def _add_callback(self, callback):
        self._metaclass._add_callback(self._namespace, callback)

    def __init__(self, metaclass, namespace):
        self._metaclass = metaclass
        self._namespace = namespace

    @property
    def __namespace__(self): return self._namespace

    @property
    def __metaclass__(self): return self._metaclass
    
    def __getattribute__(self, attr):
        if attr in __class__.local_attributes:
            return super().__getattribute__(attr)
        try:         return self._namespace[attr]
        except KeyError:
            raise AttributeError(attr) from None
        

    def __setattr__(self, attr, value):
        if attr in __class__.local_attributes:
            return super().__setattr__(attr, value)
        self._namespace[attr] = value

    def __delattr__(self, attr):
        try: del self._namespace[attr]
        except KeyError:
            raise AttributeError(attr) from None
        
    
    

class modelmethod:

    '''Usage within the body of a :class:`ModelingBase` subclass::

        @modelmethod
def method(cls, *args, **kwargs):

    Will add ``meth`` to any class using the class where this modelmethod is added as a metaclass.  The method can be called in the class body, in which case it will receive a :class:`ModelingNamespaceProxy` rather than a completed class.  Alternatively after the class body is completed, the method can be called on the class as a classmethod, in which case it will receive the class.  It is an error to call a modelmethod on a class after it  has been instantiated.


    :param cls: The namespace proxy or class.

    Remaining parameters are passed from the call to modelmethod.

    '''

    def __init__(self, meth):
        self.method = meth

    def __repr__(self):
        return f'<modelmethod: {repr(self.method)}>'


    def __call__(self, metaclass, ns, *args, **kwargs):
        proxy = ModelingNamespaceProxy(metaclass, ns)
        return self.method(proxy, *args, **kwargs)

    def __get__(self, _class, owner):
        if _class is None: return self
        if _class._already_instantiated:
            raise AttributeError('modelmethods cannot be called after instantiation')
        return functools.partial(self.method, _class)
    
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
        return ModelingNamespace(cls, filters=namespace_filters,
                                 initial=initial,
                                 classes_to_inject=classes_to_inject)

    def __new__(cls, name, bases, namespace, **kwargs):
        namespace.initial.clear()
        namespace.setitem('_already_instantiated', False)
        thread_local.current_context = None
        try:
            return super(ModelingBase, cls).__new__(cls, name, bases, namespace, **kwargs)
        except TypeError as e:
            raise TypeError(f'Error constructing ${name}: {str(e)}') from None

    def __init_subclass__(cls, *args):
        if 'namespace_filters' not in cls.__dict__:
            cls.namespace_filters = []
        if 'namespace_initial' not in cls.__dict__:
            cls.namespace_initial = {}
        for k, v in cls.__dict__.items():
            if isinstance(v, modelmethod):
                cls.namespace_initial[k] = v  # Parsed further in the namespace constructor


__all__ += ["ModelingBase"]

#: An injection key to store the set of keys that could have been transcluded but were not so that container propagation does not  transclude
not_transcluded_key = InjectionKey('carthage.modeling.not_transcluded', _optional=True)

__all__ += ['not_transcluded_key']


class InjectableModelType(ModelingBase):

    classes_to_inject: typing.Sequence[type] = (NetworkConfig, carthage.console.CarthageRunnerCommand)
    _callbacks: typing.List[typing.Callable]

    def _handle_provides(target_cls, ns, k, state):
        if hasattr(state.value, '__provides_dependencies_for__'):
            state.extra_keys.extend(state.value.__provides_dependencies_for__)
        if hasattr(state.value, '__transclusion_key__'):
            assert state.value.__transclusion_key__ in state.extra_keys, \
                f'{state.value.__transclusion_key__} must be provided by {state.value}'
            state.transclusion_key = state.value.__transclusion_key__
            

    namespace_filters = [_handle_provides]

    @classmethod
    def _add_callback(cls, ns: ModelingNamespace, cb: typing.Callable):
        ns.setdefault('_callbacks', [])
        ns['_callbacks'].append(cb)

    @classmethod
    def __prepare__(cls, name, bases, **kwargs):
        namespace = super().__prepare__(cls, name, bases, **kwargs)
        to_inject = namespace.to_inject
        transclusions = namespace.transclusions
        namespace.setitem('__transclusions__', transclusions)
        namespace.setitem('__initial_injections__', to_inject)
        for c in reversed(bases):
            if hasattr(c, '__initial_injections__'):
                to_inject.update(c.__initial_injections__)
            if hasattr(c, '__transclusions__'):
                transclusions.update(c.__transclusions__)
        return namespace

    def __new__(cls, name, bases, namespace, **kwargs):
        namespace.setdefault('_callbacks', [])

        self = super(InjectableModelType, cls).__new__(cls, name, bases, namespace, **kwargs)
        if self.__transclusions__:
            inject(_not_transcluded=not_transcluded_key)(self)

        return self

    def __init_subclass__(cls, *args, **kwargs):
        if 'classes_to_inject' not in cls.__dict__:
            cls.classes_to_inject = []
        super().__init_subclass__(*args, **kwargs)

    @modelmethod
    def add_provider(cls,  k: InjectionKey,
                     v: typing.Any = None,
                     close=True,
                     allow_multiple=False, globally_unique=False,
                     propagate=False,
                     transclusion_overrides=False,
                     force_multiple_instantiate=False):
        '''

        Similar to :meth:`Injector.add_provider` except as a model method.  *close* and *allow_multiple* work the same as on an Injector.

        :param propagate: If ``True``, then perform container propagation as this dependency moves up to ward the base injector.

        :param transclude_overrides: If ``True``,  when this :class:`InjectableModel` is instantiated, only add *k* as a provided depedency if *k* is not already in the injector hierarchy.  In effect, allow an existing provider for *k* to mask *p* at instantiation time; see :func:`transclude_overrides`.

        :param force_multiple_instantiate: Normally it is an error to call *add_provider* where the provider is itself a subclass of :class:`InjectableModel` that provides its own dependencies.  Were that to be allowed, multiple instances of the same model would be instantiated; wrapping the provider in :class:`injector_access` is almost certainly what is desired.  But if it actually is desirable to instantiate multiple instances of the same model, setting *force_multiple_instantiate* will suppress the error.

        '''

        to_inject = cls.__initial_injections__
        to_propagate = getattr(cls, '__container_propagations__', None)
        transclusions = cls.__transclusions__
        metaclass = getattr(cls, '__metaclass__', cls.__class__)
        if not isinstance(k, InjectionKey) and v is None:
            v = k
            k = default_injection_key(k)
        if globally_unique:
            warnings.warn(
                "Globally_unique is deprecated; set _globally_unique on the key",
                DeprecationWarning,
                stacklevel=2)
            k = InjectionKey(k, _globally_unique=True)
        if isinstance(v,InjectableModelType) and not force_multiple_instantiate:
            existing_key = check_already_provided(cls, v)
            if existing_key:
                raise SyntaxError(f'{v} is already provided by {existing_key}; wrap it in injector_access, or in the unlikely case that multiple instantiation is desired, set force_multiple_instantiate')
            
        if transclusion_overrides:
            transclusions[k] = {k}
        to_inject[k] = (v, dict(
            close=close,
            allow_multiple=allow_multiple,
        ))
        if propagate:
            assert issubclass(metaclass, ModelingContainer), "Only ModelingContainers accept propagation"
            to_propagate.add(k)

    @modelmethod
    def disable_system_dependency(cls, dependency):
        cls.__initial_injections__[dependency.default_instance_injection_key()] = (
            None, dict(close=False, allow_multiple=False))

    @modelmethod
    def self_provider(cls,  k: InjectionKey=None):
        def callback(inst):
            nonlocal k
            if k is None: k = inst.default_instance_injection_key()
            inst.injector.add_provider(k, dependency_quote(inst))
        cls._add_callback( callback)


__all__ += ['InjectableModelType']



class ModelingContainer(InjectableModelType):

    #: Returns the key under which we are registered in the parent.  Our
    # objects will be adapted by adding these constraints to register in
    # the parent as well.
    our_key: typing.Callable[[object], InjectionKey]

    def _integrate_containment(target_cls, ns, k, state):
        def propagate_provider(outer_key, k_inner, v, options, do_global):
            # There is the set of outer keys by which the container is
            # known; typically from @provides_dependencies_for or
            # @globally_unique_key, and there is a set of inner keys
            # from the providers being injected (from @propagate_key
            # or add_provider with propagate True).  We want to pick
            # one outer key and also add the inner keys (plus the
            # constraints from the outer key) into the outer
            # injector. This function will be called for each outer
            # key to propagate the crossproduct of inner and outer
            # keys.  

            globally_unique = k_inner.globally_unique
            if not globally_unique:
                if k_inner in val.__transclusions__:
                    raise TypeError(f'{k_inner} cannot be transcluded and propagated unless it is globally unique')
                outer_constraints = outer_key.constraints
                if set(outer_constraints) & set(k_inner.constraints):
                    return
                k_new = InjectionKey(k_inner.target, **outer_constraints, **k_inner.constraints)
            else:
                if not do_global: return
                k_new = k_inner  # globally unique
            # Must be after we have chosen a globally unique key if there is one.
            if k_new not in to_inject and k_inner not in inner_key_map:
                inner_key_map[k_inner] = k_new
            if isinstance(v, decorators.injector_access):
                # Normally injector_access will not actually get a key
                # in __initial_injections__, but there are important
                # cases where that happens, like the machine entry on
                # MachineModel.  In general this situation will come
                # up if some modeling type wishes to propagate
                # something that is referenced by an InjectionKey
                # rather than a value.  
                k_inner = v.key
            # In some cases we could make the injector_xref more clear
            # by adding the outer key's constraints to
            # v.injectable_key and use that as the new injectable key,
            # preserving the inner target key.  That generally works
            # so long as the injectable_key is not masked by a key
            # already in the outer injector or by an overlapping
            # constraint.  Also, It just moves the recursion around.
            v = injector_xref(outer_key, k_inner)
            if k_new not in to_inject:
                to_inject[k_new] = (v, options)
                outer_to_propagate.add(k_new)

        if not isinstance(state.value, ModelingContainer):
            return
        inner_key_map = {}
        val = state.value
        if isinstance(ns, ModelingNamespace):
            ns = ModelingNamespaceProxy(ns.cls, ns)
        outer_key = None
        to_inject = ns.__initial_injections__
        outer_to_propagate = ns.__container_propagations__
        to_propagate = val.__container_propagations__
        outer_keys = []
        if hasattr(val, '__provides_dependencies_for__'):
            for outer_key in sorted(
                    val.__provides_dependencies_for__,
                    key=lambda k: k.globally_unique,
                    reverse=True):
                if outer_key not in outer_to_propagate: continue
                if  len(outer_key.constraints) > 0:
                    outer_keys.append(outer_key)
        if to_propagate and not outer_keys:
            warnings.warn("Cannot propagate because no outer key with constraints found", stacklevel=3)
            return
        for k_inner in to_propagate:
            if k_inner not in val.__initial_injections__:
                warnings.warn(f'Cannot propagate {k_inner} because it is not provided by {val}')
                continue
            v, options = val.__initial_injections__[k_inner]
            do_global = True
            for outer_key in outer_keys:
                propagate_provider(outer_key, k_inner, v, options, do_global)
                do_global = False
        map_transclusions(ns.__transclusions__, val, inner_key_map)

    def _propagate_filter(target_cls, ns, k, state):
        if isinstance(ns, ModelingNamespace):
            ns = ModelingNamespaceProxy(ns.cls, ns)
        if hasattr(state.value, '__container_propagation_keys__'):
            propagation_keys = set(state.value.__container_propagation_keys__)
        else: propagation_keys = set()
        value_keys = set((x[0] for x in keys_for(name=k, state=state, classes_to_inject=set())))
        propagation_keys &= value_keys
        # ModelingContainers must propagate even if they have no explicit keys
        if (not propagation_keys ) and isinstance(state.value, ModelingContainer):
            propagation_keys = value_keys
        if propagation_keys: 
            ns.__container_propagations__  |= propagation_keys
        
    # propagate_filter must come before integrate_containment so that
    # integrate_containment can check whether outer_keys are in
    # ns.to_propagate. However, filters are processed in reversed
    # order.
    namespace_filters = [_integrate_containment, _propagate_filter]

    @classmethod
    def __prepare__(cls, *args, **kwargs):
        ns = super().__prepare__(*args, **kwargs)
        ns.to_propagate = set()
        ns.setitem('__container_propagations__', ns.to_propagate)
        return ns

    def __new__(cls, name, bases, ns, **kwargs):
        to_propagate = ns.to_propagate
        self = super().__new__(cls, name, bases, ns, **kwargs)
        to_propagate |= set(
            propagations for b in bases
            for propagations in getattr(b, '__container_propagations__', set()))
        for k in set(to_propagate): #copy for mutation
            if k not in self.__initial_injections__:
                self.__container_propagations__.remove(k)
        return self

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            our_key = self.our_key()
            if not isinstance(our_key, InjectionKey):
                our_key = InjectionKey(our_key)
            setattr_default(self, '__provides_dependencies_for__', [])
            self.__provides_dependencies_for__.insert(0, our_key)
        except (AttributeError, NameError):
            pass

    @modelmethod
    def include_container(cls, obj,
                          *, close=True,
                          allow_multiple=False,
                          dynamic_name=None,
                          **kwargs):
        if dynamic_name and not hasattr(cls, '__namespace__'):
            raise TypeError('include_container can only be called within a class body if dynamic_name is used')
        def handle_function(func):
            params = set(inspect.signature(func).parameters.keys())
            open_params = params - set(kwargs.keys())
            for k in open_params:
                if not hasattr(cls, k):
                    raise AttributeError(f'{k} not found in enclosing class')
                kwargs[k] = getattr(cls, k)
            return func(**kwargs)
        if not hasattr(obj, '__provides_dependencies_for__') and callable(obj):
            obj = handle_function(obj)
        if (not hasattr(obj, '__provides_dependencies_for__')) and not dynamic_name:
            raise TypeError(f'{obj} is not a modeling container class')
        state = NsEntry(obj)
        if dynamic_name:
            # This is gross, but it's not clear how to support
            # decorators ourselves
            if (not close) or allow_multiple:
                raise TypeError('If using dynamic_name, use decorators to adjust close and allow_multiple')
            setattr(cls, fixup_dynamic_name(dynamic_name), obj)
            return
        # Since we're not able to use ns.__setitem__, do the injection key stuff ourself.
        if not close:
            state.flags &= ~NSFlags.close
        if allow_multiple:
            state.flags |= NSFlags.allow_multiple
        state.flags &= ~NSFlags.inject_by_name
        state.extra_keys = obj.__provides_dependencies_for__
        options = state.injection_options
        for k, _ in keys_for(name="", state=state, classes_to_inject=set()):
            if k not in cls.__initial_injections__:
                cls.__initial_injections__[k] = (obj, options)
        ModelingContainer._propagate_filter(cls, cls, obj.__name__, state)
        ModelingContainer._integrate_containment(cls, cls, obj.__name__, state)



__all__ += ['ModelingContainer']


def adjust_bases_for_tasks(bases: tuple[type], namespace: dict) -> tuple[type]:
    '''
If the namespace includes any setup_tasks, then add SetupTaskMixin to the baseses.
    '''
    from ..setup_tasks import SetupTaskMixin, TaskWrapper
    if SetupTaskMixin in bases:
        return bases
    for b in bases:
        if SetupTaskMixin in b.__mro__: return bases
    for v in namespace.values():
        if isinstance(v, TaskWrapper):
            break
    else:
        return bases
    new_bases = [*bases]
    for i, c in enumerate(new_bases):
        if AsyncInjectable in c.__mro__:
            new_bases.insert(i, SetupTaskMixin)
            return tuple(new_bases)
    else:
        new_bases.extend([SetupTaskMixin,AsyncInjectable])
    return tuple(new_bases)


__all__ += ['adjust_bases_for_tasks']


def _handle_base_customization(target_cls, ns, k, state):
    val = state.value
    if not state.flags & NSFlags.instantiate_on_access:
        return
    if not isinstance(val, type):
        return
    if not issubclass(val, carthage.machine.BaseCustomization):
        return
    if state.flags & NSFlags.inject_by_class:
        key = val.default_class_injection_key()
        key = InjectionKey(carthage.machine.BaseCustomization, **key.constraints)
        ns.to_inject[key] = dependency_quote(val), state.injection_options
    state.flags &= ~(NSFlags.inject_by_class | NSFlags.instantiate_on_access)
    state.value = decorators.wrap_base_customization(val, state.new_name or k)


__all__ += ['_handle_base_customization']


def add_provider_after(cls, k: InjectionKey,
                       v: typing.Any = None,
                       close=True,
                       allow_multiple=False,
                       propagate=False,
                       transclusion_overrides=False):
    '''This is a hack two allow you to call add_provider in init_subclass.
This must happen prior to the first instantiation of a subclass, and for propagation at least currently prior to the first time the class is put in a container.
    Long term we will rework modelmethod to also add a class method usable prior to instantiation, and rework propagation so that it happens in instance initialization (or at least containing class initialization) time to give more room for adjusting.
    This will be romeved then.
    '''
    if not isinstance(k, InjectionKey) and v is None:
        v = k
        k = default_injection_key(k)
    if transclusion_overrides:
        cls.__transclusions__.add((k, k, cls))
    cls.__initial_injections__[k] = (v, dict(
        close=close,
        allow_multiple=allow_multiple,
    ))
    if propagate:
        assert isinstance(cls, ModelingContainer), "Only ModelingContainers accept propagation"
        cls.__container_propagations__.add(k)


def map_transclusions(container_transclusions, contained, inner_key_map):
    '''
    Consider the case when *contained* is added to *container*.  *contained*  provides a dependency *k*, but that dependency permits transclusion.  That means that if *contained* is instantiated in an injector  that already provides *k*, then the instantiating injector's version of *k* is used.

    It's an error for *k* not to be globally unique.  Transcluded keys must either be globally unique or not propagated, because transclusion always happens in the scope of the instantiating injector, and the only time when that naming is the same as the inner injector (for objects provided by the inner injector) is for globally unique keys.  However, when *k* is transcluded, there may be extra keys *k_1* that are also suppressed because they are provided by the same object as *k*.  These keys need to be remapped from *contained*'s transclusions to *container*'s transclusions.

    :param inner_key_map: A mapping from inner keys to outer keys.

    :param container_transclusions:  The **__transclusions__* mapping of the namespace that will become *container*.
    '''
    transclusions = container_transclusions
    inner_transclusions = contained.__transclusions__
    for transcluded_key in set(inner_transclusions) & set(inner_key_map):
        transcluded_keys = transclusions.setdefault(transcluded_key, set())
        transcluded_keys.update({inner_key_map[ik] for ik in inner_transclusions[transcluded_key] if ik in inner_key_map})
    
from . import decorators
