from carthage import dependency_injection
from carthage.dependency_injection import inject

import pytest

@pytest.fixture()
def injector():
    return dependency_injection.Injector()

def test_injector_provides_self(injector):
    @inject(i = dependency_injection.Injector)
    def func(i):
        return i
    assert isinstance(injector(func), dependency_injection.Injector)


def test_injector_available(injector):
    assert isinstance(injector, dependency_injection.Injector)
    

def test_override_dependency(injector):
    k = dependency_injection.InjectionKey('some key')
    injector.add_provider(k,30)
    @inject(arg = k)
    def func(arg):
        assert arg == 20
    injector(func, arg = 20)
    # And make sure without the override the injector still provides the right thing
    @inject(i = k)
    def func2(i):
        assert i == 30
    injector(func2)

def test_override_replaces_subinjector(injector):
    class OverrideType: pass
    o1 = OverrideType()
    o2 = OverrideType()
    assert o1 is not o2
    @inject(o = OverrideType,
            i = dependency_injection.Injector)
    def func(i, o):
        assert o is o2
        assert injector is not i
        assert i.parent_injector is injector
    @inject(o = OverrideType)
    def func2(o):
        assert o is o1
    injector.add_provider(o1)
    injector(func, o = o2)
    injector(func2)
    

