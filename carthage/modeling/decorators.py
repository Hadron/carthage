# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .implementation import ModelingBase, InjectableModelType, ModelingContainer
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
        ns[self.new_name] = value
        return True

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
        self,
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
            return val
    return wrapper


__all__ = ["ModelingDecoratorWrapper", "provides", 'dynamic_name',
           'globally_unique_key',
           ]
