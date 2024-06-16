# Copyright (C) 2021, 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import functools
import typing
from carthage.dependency_injection import Injector, InjectionKey, inject_autokwargs, dependency_quote, AsyncRequired, inject
from .implementation import ModelingBase, InjectableModelType, ModelingContainer, NSFlags
from .utils import setattr_default, fixup_dynamic_name
from ..dependency_injection import InjectionKey
import carthage.machine


class ModelingDecoratorWrapper:

    subclass: type = ModelingBase
    name: str

    def __init__(self, value):
        self.value = value

    def __repr__(self):
        return f'{self.__class__.name}({repr(self.value)})'

    def handle(self, cls, ns, k, state):
        if not issubclass(cls, self.subclass):
            raise TypeError(f'{self.__class__.name} decorator only for subclasses of {self.cls.__name__}')
        state.value = self.value
        if isinstance(self.value, ModelingDecoratorWrapper):
            if self.value.handle(cls, ns, k, state):
                raise TypeError("A decorator that suppresses assignment must be outermost")
            else:
                self.value = state.value  # in case superclasses care

    @property
    def __provides_dependencies_for__(self): return self.value.__provides_dependencies_for__

    _injection_error = "Use @inject on the innermost value not on a decorator."
    
class injector_access(ModelingDecoratorWrapper):

    '''Usage::

        val = injector_access("foo")

    At runtime, ``model_instance.val`` will be memoized to ``model_instance.injector.get_instance("foo")``.

    '''

    # Unlike most ModelingDecorators we want to get left in the
    # namespace.  The only reason this is a ModelingDecorator is so we
    # can clear the inject_by_name flag to avoid namespace polution and
    # to minimize the probability of a circular references from
    # something like ``internet = injector_access("internet")``

    key: InjectionKey
    name: str
    target: type

    def __init__(self, key, target=None):
        super().__init__(key)
        if not isinstance(key, InjectionKey):
            if isinstance(key, InjectableModelType) and key.__provides_dependencies_for__ and target is None:
                key = key.__provides_dependencies_for__[0]
                target = key
            else:
                key = InjectionKey(key, _ready=False)
        if key.ready is None:
            key = InjectionKey(key, _ready=False)
        #: The injection key to be accessed.
        self.key = key
        self.target = target
        # Our __call__method needs an injector
        inject_autokwargs(injector=Injector)(self)

    _injection_error = None #Because injector_access does have dependencies.

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.value = self
        state.flags &= ~NSFlags.inject_by_name

    def __get__(self, inst, owner):
        if inst is None:
            if self.target:
                return self.target
            return self
        try:
            res = inst.injector.get_instance(self.key)
            setattr(inst, self.name, res)
            return res
        except KeyError as e:
            # Injectors return KeyError, but for a missing Attribute
            # we want to raise AttributeError so for example hasattr
            # will be false.
            raise AttributeError(str(e)) from e

    def __set_name__(self, owner, name):
        self.name = name

    def __call__(self, injector: Injector):
        try:
            return injector.get_instance(self.key)
        except AsyncRequired:
            @inject(injector=None,
                    injectable=InjectionKey(Injector))
            class Xref(carthage.dependency_injection.base.InjectorXrefMarker):
                target_key = self.key

            return Xref(injectable=injector)
        

    def __repr__(self):
        return f'injector_access({repr(self.key)})'


class ProvidesDecorator(ModelingDecoratorWrapper):
    subclass = InjectableModelType
    name = "provides"

    def __init__(self, value, *keys):
        super().__init__(value)
        self.keys = keys

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.extra_keys.extend(self.keys)


def provides(*keys):
    '''Indicate that the decorated value provides these InjectionKeys'''
    keys = list(map(lambda k: InjectionKey(k) if not isinstance(k, InjectionKey) else k, keys))

    def wrapper(val):
        try:
            setattr_default(val, '__provides_dependencies_for__', None)
            if val.__provides_dependencies_for__ is None:
                if isinstance(val, type):
                    val.__provides_dependencies_for__ = [
                        k for b in val.__bases__
                        for k in getattr(b, '__provides_dependencies_for__', [])]
                else:
                    val.__provides_dependencies_for__ = list(getattr(val.__class__, '__provides_dependencies_for__', []))
            val.__provides_dependencies_for__ = keys + val.__provides_dependencies_for__
            return val
        except BaseException:

            return ProvidesDecorator(val, *keys)
    return wrapper


class DynamicNameDecorator(ModelingDecoratorWrapper):

    name = "dynamic_name"

    def __init__(self, value, new_name):
        super().__init__(value)
        self.new_name = new_name

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        value = self.value
        if hasattr(value, '__qualname__'):
            value.__qualname__ = ns['__qualname__'] + '.' + self.new_name
            value.__name__ = self.new_name
        state.value = value
        state.new_name = self.new_name


def dynamic_name(name):
    '''A decorator to be used in a modeling type to supply a dynamic name.  Example::

        for i in range(3):
            @dynamic_name(f'i{i+1}')
    class ignored: square = i*i

    Will define threevariables i1 through i3, with values of squares (i1 = 0, i2 =1, i3 = 4).
    '''
    name = fixup_dynamic_name(name)

    def wrapper(val):
        return DynamicNameDecorator(val, name)
    return wrapper


def globally_unique_key(
    key: typing.Union[InjectionKey, typing.Callable[[object], InjectionKey]],
):
    '''Decorate a value to indicate that *key* is a globally unique
    :class:`~InjectionKey` that should provide the given value.
    Globally unique keys are not extended with additional constraints
    when propagated up through :class:`~ModelingContainers`.

    :param key:  A callback that maps the value to a key.  Alternatively, simply the :class:`~InjectionKey` to use.

    '''
    def wrapper(val):
        nonlocal key
        if callable(key):
            key = key(val)
        elif isinstance(key, str):
            key = InjectionKey(key)
        setattr_default(val, '__provides_dependencies_for__', [])
        if key not in val.__provides_dependencies_for__:
            val.__provides_dependencies_for__.append(InjectionKey(key, _globally_unique=True))
        return val
    return wrapper


class FlagClearDecorator(ModelingDecoratorWrapper):

    def __init__(self, value, flag: NSFlags):
        super().__init__(value)
        self.flag = flag

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.flags &= ~self.flag


def no_inject_name(val=None, /):
    def wrap(v):
        return FlagClearDecorator(v, NSFlags.instantiate_on_access|NSFlags.inject_by_name)
    if val is not None:
        return wrap(val)
    else: return wrap
    
def no_instantiate():
    def wrapper(val):
        return FlagClearDecorator(val, NSFlags.instantiate_on_access)
    return wrapper


def no_close():
    def wrapper(val):
        return FlagClearDecorator(val, NSFlags.close)
    return wrapper


def allow_multiple():
    raise NotImplementedError("Need to write FlagSetDecorator")


class MachineMixin(ModelingDecoratorWrapper):

    subclass = InjectableModelType
    name = "machine_mixin"

    def __init__(self, value, name):
        super().__init__(value)
        self.name = name

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.flags &= ~(NSFlags.inject_by_name | NSFlags.inject_by_class | NSFlags.instantiate_on_access)
        ns.to_inject[InjectionKey(MachineMixin,
                                  name=self.name)] = (
                                      dependency_quote(state.value), state.injection_options)


def machine_mixin(name=None):
    '''Mark a class (subclass of :class:`Machine` typically) as something that should be mixed in to any machine declared lower in the injector hierarchy withing modeling classes.  To accomplish the same thing outside of modeling classes::

        injector.add_provider(InjectionKey(MachineMixin, name = "some_name"), dependency_quote(mixin_class))

    The call to :func:~carthage.dependency_injection.dependency_quote`
    is required to prevent the injector from trying to build a machine
    when the Mixin is looked up.

'''
    def wrapper(val):
        return MachineMixin(val, name or val.__name__)
    return wrapper



def propagate_key(key, obj=None):
    '''Indicate a set of keys that should be propagated up
    in a container::

        class foo(ModelingContainer):
            @propagate_key(InjectionKey(Baz, target = 42))
            class ourbaz(Baz): ...

    When *foo* is included in a container, then the *Baz* injection
    key will be propagated up to dependencies provided by that
    container.  Since the key was not marked globally unique,
    constraints from *foo.our_key()* will be added to it as it is
    propagated.

    *keys* are also provided by the contained class as if :func:`provides` or :func:`globally_unique` were called.

    Propagating a key up is typically an interface point; rather than propagating all keys related to an object up, propagate the keys that will be understood by the environment.  Examples of usage include:

    * Any :class:`~carthage.machine.AbstractMachineModel` with a *host* constraint is collected to find all the machine models in a layout

    
    '''
    def wrap(val):
        setattr_default(val,'__container_propagation_keys__', None)
        if val.__container_propagation_keys__ is None:
            if isinstance(val, type):
                val.__container_propagation_keys__ = set(
                    propagation_keys for  b in val.__bases__
                    for propagation_keys in getattr(b, '__container_propagation_keys__', set()))
            else: # not type
                val.__container_propagation_keys = set(getattr(
                    val.__class__,
                    '__container_propagation_keys__',
                    set()))
        val.__container_propagation_keys__.add(key)
        return provides(key)(val)
    if not isinstance(key, InjectionKey):
        raise TypeError('propagate_key takes an injection key and optional object to apply it to.')
    if obj is not None:
        return wrap(obj)
    return wrap


def transclude_overrides(
        key: InjectionKey = None):
    '''
    Decorator indicating that the decorated item should be overridden by one from the injector against which the containing layout is eventually instantiated.  When a :class:`InjectableModel` is eventually instantiated, before an overridable key is added to the local injector, it is searched for in the instantiating injector.  If it is found, the  key is not registered.  This has the effect of using the dependency provider in the instantiating injector rather than the one included in the layout.

    :param key: If supplied, is the key expected to be registered with the transcluding injector to override  this object.  If not supplied, the object must have a globally unique key.



    Example usage::

        class layout(CarthageLayout):

            @transclude_overrides(key=InjectionKey("network"))
            class network(NetworkModel):
                # ...

    If ``layout`` is instantiated in a injector that provides a dependency for ``InjectionKey('network')``, then that object will be used rather than the ``network`` class within the layout.  Since the ``network`` property is an :func:`injector_acess`, ``layout_instance.network`` will refer to the object in the instantiating injector rather than an instance of ``layout.network``.


    '''
    def wrap(val):
        nonlocal key
        if key is None:
            for k in val.__provides_dependencies_for__:
                if k.globally_unique:
                    key = k
                    break
            if key is None:
                raise ValueError('A globally unique key is required')
        val.__transclusion_key__ = key
        return val
    return wrap



def model_mixin_for(**constraints):
    '''
    Indicate that a given model supplements a model declared automatically.    The simplest usage looks like::

        @model_mixin_for(host = "foo.com")
        class foomixin(MachineModel):
            # stuff added to foo.com

        class foo(MachineModel):
            name = "foo.com"
            # Also inherits from foomixin

    In the above example it would be simpler to list *foomixin* as a base for *foo*.  However when a loop instantiates a number of models with a dynamic name, *model_mixin_for* provides value::

        for c in ('foo', 'bar', 'baz'):
            @dynamic_name(c)
            class model(MachineModel):
                name = c+".com"

    In the above usage, the loop would need to get more complicated to only add *foomixin* to the dynamically generated class for the ``foo.com`` model.  In this case *model_mixin_for* provides value.

    IN a more complex usage, *model_mixin_for* can be used in a layout that will transclude a model using :func:`carthage.modeling.base.model_bases`.  This is similar to :func:`transclude_overrides` except that rather than entirely replacing the model, *model_mixin_for* simply adds a base.

    '''
    from .base import MachineModelMixin

    def wrap(val):
        if isinstance(val, ModelingDecoratorWrapper):
            # Assume it is already a model_mix in_for which we're stacking
            return provides(InjectionKey(MachineModelMixin, **constraints))(val)
        return provides(InjectionKey(
            MachineModelMixin, **constraints))(dependency_quote(val))
    return wrap


class wrap_base_customization:

    def __init__(self, val, name):
        self.name = name
        functools.wraps(val)(self)
        self.value = val

    def __get__(self, instance, owner):
        if instance is None:
            return self.value
        machine = instance.injector.get_instance(InjectionKey(carthage.machine.Machine, _ready=False))
        return instance.injector(self.value, apply_to=machine, stamp=self.name)


__all__ = ["ModelingDecoratorWrapper", "provides", 'dynamic_name',
           'injector_access', 'no_inject_name', 'no_instantiate',
           'allow_multiple', 'no_close',
           'globally_unique_key',
           'MachineMixin', 'machine_mixin',
           'propagate_key',
           'transclude_overrides',
           'model_mixin_for',
           ]
