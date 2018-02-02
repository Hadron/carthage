# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import dependency_injection
from carthage.dependency_injection import inject, InjectionKey
from test_helpers import async_test

import asyncio, pytest

@pytest.fixture()
def injector():
    return dependency_injection.Injector()

@pytest.fixture()
def a_injector(injector, loop):
    return injector(dependency_injection.AsyncInjector, loop = loop)

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
    



def test_injector_instantiates(injector):
    class SomeClass(dependency_injection.Injectable): pass
    @inject(s = SomeClass)
    def func(s):
        assert isinstance(s, SomeClass)
    injector.add_provider(SomeClass)
    injector(func)
    
def test_async_injector_construction(loop, injector):
    @inject(a = dependency_injection.AsyncInjector)
    def f(a):
        assert isinstance(a,dependency_injection.AsyncInjector)
    injector.add_provider(loop)
    injector(f)
    

@async_test
async def test_construct_using_coro(a_injector, loop):
    async def coro():
        return 42
    k = dependency_injection.InjectionKey('run_coro')
    @inject(v = k)
    def f(v):
        assert v == 42
    a_injector.add_provider(k, coro)
    await a_injector(f)

@async_test
async def test_async_function(a_injector, loop):
    class Dependency(dependency_injection.Injectable): pass
    async def setup_dependency(): return Dependency()
    called = False
    @inject(d = Dependency)
    async def coro(d):
        assert isinstance(d, Dependency)
        nonlocal called
        called = True
    a_injector.add_provider(InjectionKey(Dependency), setup_dependency)
    await a_injector(coro)
    assert called is True
    
    

@async_test
async def test_async_ready(a_injector, loop):
    class AsyncDependency(dependency_injection.AsyncInjectable):
        async def async_ready(self):
            self.ready = True
            return self

        def __init__(self):
            self.ready = False
    @inject(r = AsyncDependency)
    def is_ready(r):
        assert r.ready
    await a_injector(is_ready, r = AsyncDependency)
    
