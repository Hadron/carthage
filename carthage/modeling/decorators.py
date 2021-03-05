# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.dependency_injection import Injector, InjectionKey, inject_autokwargs
from .implementation import ModelingBase, InjectableModelType, ModelingContainer, NSFlags, handle_transclusions
from .utils import setattr_default
import typing
from ..dependency_injection import InjectionKey


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
                self.value = state.value #in case superclasses care

    @property
    def __provides_dependencies_for__(self): return self.value.__provides_dependencies_for__

    @property
    def __globally_unique_key__(self): return self.value.__globally_unique_key__


class injector_access(ModelingDecoratorWrapper):

    '''Usage::

        val = injector_access("foo")

    At runtime, ``model_instance.val`` will be memoized to ``model_instance.injector.get_instance("foo")``.

    '''

    #Unlike most ModelingDecorators we want to get left in the
    #namespace.  The only reason this is a ModelingDecorator is so we
    #can clear the inject_by_name flag to avoid namespace polution and
    #to minimize the probability of a circular references from
    #something like ``internet = injector_access("internet")``

    key: InjectionKey
    name: str
    target: type

    def __init__(self, key, target = None):
        super().__init__(key)
        if not isinstance(key, InjectionKey):
            key = InjectionKey(key, _ready = False)
        if key.ready is None: key = InjectionKey(key, _ready = False)
        #: The injection key to be accessed.
        self.key = key
        self.target = target
        # Our __call__method needs an injector
        inject_autokwargs(injector = Injector)(self)

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.value = self
        state.flags &= ~NSFlags.inject_by_name

    def __get__(self, inst, owner):
        if inst is None:
            if self.target: return self.target
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
        return injector.get_instance(self.key)
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
    keys = list(map( lambda k: InjectionKey(k), keys))
    def wrapper(val):
        try:
            setattr_default(val, '__provides_dependencies_for__', [])
            val.__provides_dependencies_for__ = keys+val.__provides_dependencies_for__
            return val
        except:

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
            value.__qualname__ = ns['__qualname__']+'.'+self.new_name
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
        val.__globally_unique_key__ = key
        #Make sure we're providing the key as well.
        setattr_default(val, '__provides_dependencies_for__', [])
        if key not in val.__provides_dependencies_for__:
            val.__provides_dependencies_for__.append(key)
        return val
    return wrapper


class FlagClearDecorator(ModelingDecoratorWrapper):

    def __init__(self, value, flag: NSFlags):
        super().__init__(value)
        self.flag = flag
    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.flags &= ~self.flag

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
        super().handle(cls, ns, k. state)
        state.flags &= ~(NSFlags.inject_by_name | NsFlags.inject_by_class | NSFlags.instantiate_on_access)
        ns.to_inject[InjectionKey(MachineMixin,
                                  name = self.name)] = (
                                      dependency_quote(state.value), state.injection_options)

def machine_mixin(name = None):
    '''Mark a class (subclass of :class:`Machine` typically) as something that should be mixed in to any machine declared lower in the injector hierarchy withing modeling classes.  To accomplish the same thing outside of modeling classes::

        injector.add_provider(InjectionKey(MachineMixin, name = "some_name"), dependency_quote(mixin_class))

    The call to :func:~carthage.dependency_injection.dependency_quote`
    is required to prevent the injector from trying to build a machine
    when the Mixin is looked up.

'''
    def wrapper(val):
        return MachineMixin(val, name or val.__name__)
    return wrapper

class PropagateUpDecorator(ModelingDecoratorWrapper):

    name = "propagate_up"
    subclass = ModelingContainer

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.flags |= NSFlags.propagate_key

def propagate_up():
    '''Indicate that an assignment should have its keys propagated up in
    a container::

        class foo(ModelingContainer):
            @propagate_up()
            @provides(InjectionKey(Baz, target = 42))
            class ourbaz(Baz): ...

    When *foo* is included ain a container, then the *Baz* injection
    key will be propagated up to dependencies provided by that
    container.  Since the key was not marked globally unique,
    constraints from *foo.our_key()* will be added to it as it is
    propagated.

    '''
    def wrap(val):
        return PropagateUpDecorator(val)
    return wrap

class TranscludeOverrideDecorator(ModelingDecoratorWrapper):

    name = 'transclude_overrides'

    def __init__(self, val, key):
        super().__init__(val)
        self.key = key

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        state.flags |= NSFlags.instantiate_on_access
        state.transclusion_key = self.key
        ns.transclusions.add((self.key,self.key))
        
def transclude_overrides(injector:Injector = None,
                         key:InjectionKey = None):
    '''Decorator indicating that the decorated item should be overridden by one from the injector against which the containing layout is eventually instantiated.

    :param key: If supplied, is the key expected to be registered with the transcluding injector to override  this object.  If not supplied, the object must have a globally unique key.

    :param injector: If supplied, and the key exists in the injector
    or its parents, then replace this object at modeling time with the
    uninstantiated provider of  the key from *injector*.  This does not
    affect which object is chosen at run time, but rather affects
    which object is used for class attribute access on the resulting
    modeling class.

    Ultimately the key will be looked up in the injector supplied as a dependency to the instantiated class.  Whether the transclusion happens is entirely dependent on what keys are in the injector at modeling time.

    If *injector* is not supplied, then the transclusions will
    propagate up.  They will be resolved when a containing class is
    decorated either with *transclude_overrides* with an injector
    supplied or is decorated with *transclude_injector*.  If a
    containing class is never decorated with one of these decorators,
    transclusion never happens.

    '''
    def wrap(val):
        nonlocal key
        if key is None:
            key = val.__globally_unique_key__
        if injector:
            target = injector.injector_containing(key)
            if target:
                # We intentionally throw away val.  We break the
                # abstraction of the injector, because we want to get
                # a potentially uninstantiated provider.  There are
                # various ways in which this can give disappointing
                # results, but also ways in which it can provide the
                # most consistent transclusions in common cases.
                return injector_access(key, target._providers[key].provider)
            else: #injector but doesn't contain key
                handle_transclusions(val, injector = injector)
                return val # not transcluded.
        #no injector supplied
        return TranscludeOverrideDecorator(val, key)
    return wrap

def transclude_injector(injector):
    '''
    Decorator indicating that the supplied injector should be used to resolve :func:`transclusions <transclude_overrides>`.

    '''
    def wrap(val):
        handle_transclusions(val, injector = injector)
        return val
    return wrap


__all__ = ["ModelingDecoratorWrapper", "provides", 'dynamic_name',
           'injector_access', 'no_instantiate',
           'allow_multiple', 'no_close',
           'globally_unique_key',
           'MachineMixin', 'machine_mixin',
           'propagate_up',
           'transclude_overrides', 'transclude_injector',
           ]
