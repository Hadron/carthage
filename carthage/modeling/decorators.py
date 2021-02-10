from .implementation import ModelingBase, InjectableModelType
from .utils import setattr_default


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
        state.value =  self.value

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
    def wrapper(val):
        try:
            setattr_default(val, '__provides_dependencies_for__', [])
            val.__provides_dependencies_for__ = keys+val.__provides_dependencies_for__
        except:
            return ProvidesDecorator(val, *keys)
    return wrapper

class DynamicNameDecorator(ModelingDecoratorWrapper):

    def __init__(self, value, new_name):
        super().__init__(value)
        self.new_name = new_name

    def handle(self, cls, ns, k, state):
        super().handle(cls, ns, k, state)
        value = self.value
        while isinstance(value, ModelingDecoratorWrapper):
            value.handle(cls, ns, k, state)
            value = state.value
        if hasattr(value, '__qualname__'):
            value.__qualname__ = ns['__qualname__']+'.'+self.new_name
            value.__name__ = self.new_name
        ns[self.new_name] = value
        return True

def dynamic_name(name):
    '''A decorator to be used in a modeling type to supply a dynamic name.  Example::

        for i in range(3):
            @dynamic_name(f'{i+1}')
    class ignored: square = i*i

    Will define threevariables i1 through i3, with values of squares (i1 = 0, i2 =1, i3 = 4).
    '''
    def wrapper(val):
        return DynamicNameDecorator(val, name)
    return wrapper

    
__all__ = ["ModelingDecoratorWrapper", "provides", 'dynamic_name']
