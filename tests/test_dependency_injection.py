# Copyright (C) 2018, 2019, 2020, 2021, 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import dependency_injection
from carthage.dependency_injection import *
from carthage.dependency_injection.introspection import *
from carthage.utils import when_needed
from carthage.pytest import async_test
from carthage_test_utils import Trigger

import asyncio
import pytest


@pytest.fixture()
def injector():
    injector = dependency_injection.Injector()
    injector.add_provider(asyncio.get_event_loop(), close=False)
    return injector


@pytest.fixture()
def a_injector(injector, loop):
    a_injector = injector(dependency_injection.AsyncInjector, loop=loop)
    yield a_injector
    a_injector.close()


def test_injector_provides_self(injector):
    @inject(i=dependency_injection.Injector)
    def func(i):
        return i
    assert isinstance(injector(func), dependency_injection.Injector)


def test_injector_available(injector):
    assert isinstance(injector, dependency_injection.Injector)


def test_override_dependency(injector):
    k = dependency_injection.InjectionKey('some key')
    injector.add_provider(k, 30)

    @inject(arg=k)
    def func(arg):
        assert arg == 20
    injector(func, arg=20)
    # And make sure without the override the injector still provides the right thing

    @inject(i=k)
    def func2(i):
        assert i == 30
    injector(func2)


def test_override_replaces_subinjector(injector):
    class OverrideType:
        pass
    o1 = OverrideType()
    o2 = OverrideType()
    assert o1 is not o2

    @inject(o=OverrideType,
            i=dependency_injection.Injector)
    def func(i, o):
        assert o is o2
        assert injector is not i
        assert i.parent_injector is injector

    @inject(o=OverrideType)
    def func2(o):
        assert o is o1
    injector.add_provider(o1)
    injector(func, o=o2)
    injector(func2)


def test_injector_instantiates(injector):
    class SomeClass(dependency_injection.Injectable):
        pass

    @inject(s=SomeClass)
    def func(s):
        assert isinstance(s, SomeClass)
    injector.add_provider(SomeClass)
    injector(func)


def test_async_injector_construction(loop, injector):
    @inject(a=dependency_injection.AsyncInjector)
    def f(a):
        assert isinstance(a, dependency_injection.AsyncInjector)

    injector(f)


@async_test
async def test_construct_using_coro(a_injector, loop):
    async def coro():
        return 42
    k = dependency_injection.InjectionKey('run_coro')

    @inject(v=k)
    def f(v):
        assert v == 42
    a_injector.add_provider(k, coro)
    await a_injector(f)


@async_test
async def test_async_function(a_injector, loop):
    class Dependency(dependency_injection.Injectable):
        pass

    async def setup_dependency(): return Dependency()
    called = False

    @inject(d=Dependency)
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
            await super().async_ready()
            return self

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.ready = False

    @inject(r=AsyncDependency)
    def is_ready(r):
        assert r.ready
    await a_injector(is_ready, r=AsyncDependency)


def test_allow_multiple(injector):
    from carthage.config import ConfigLayout
    injector.add_provider(ConfigLayout, allow_multiple=True)
    s1 = dependency_injection.Injector(injector)
    s2 = dependency_injection.Injector(injector)
    assert s1 is not s2
    assert s1.parent_injector is injector
    assert s2.parent_injector is injector
    c1 = s1.get_instance(ConfigLayout)
    c2 = s2.get_instance(ConfigLayout)
    assert isinstance(c1, ConfigLayout)
    assert isinstance(c2, ConfigLayout)
    assert c1 is not c2
    c3 = injector.get_instance(ConfigLayout)
    assert isinstance(c3, ConfigLayout)
    assert c3 is not c1
    assert c3 is not c2


def test_allow_multiple_provider_at_root(injector):
    from carthage.config import ConfigLayout
    injector.add_provider(ConfigLayout, allow_multiple=True)
    s1 = dependency_injection.Injector(injector)
    s2 = dependency_injection.Injector(injector)
    assert s1 is not s2
    c3 = injector.get_instance(ConfigLayout)
    c1 = s1.get_instance(ConfigLayout)
    c2 = s2.get_instance(ConfigLayout)
    assert c3 is c1
    assert c2 is c3


def test_allow_multiple_false(injector):
    from carthage.config import ConfigLayout
    injector.add_provider(ConfigLayout, allow_multiple=False)
    s1 = dependency_injection.Injector(injector)
    s2 = dependency_injection.Injector(injector)
    assert s1 is not s2
    c1 = s1.get_instance(ConfigLayout)
    c2 = s2.get_instance(ConfigLayout)
    assert c1 is c2


@async_test
async def test_when_needed(a_injector, loop):
    class foo(dependency_injection.Injectable):

        def __init__(self):
            nonlocal called
            assert called is False
            called = True

    wn = when_needed(foo)
    i1 = InjectionKey('i1')
    i2 = InjectionKey('i2')
    called = False
    a_injector.add_provider(i1, wn)
    a_injector.add_provider(i2, wn)
    i1r = await a_injector.get_instance_async(i1)
    assert isinstance(i1r, foo)
    i2r = await a_injector.get_instance_async(i2)
    assert i2r is i1r
    assert await a_injector(wn) is i1r
    assert called


@async_test
async def test_when_needed_override(a_injector, loop):
    k = dependency_injection.InjectionKey('foo')
    a_injector.add_provider(k, 20)

    @dependency_injection.inject(n=k)
    def func(n):
        assert n == 29
        return "foo"
    wn = when_needed(func, n=29)
    assert await a_injector(wn) == "foo"


@async_test
async def test_when_needed_cancels(loop, a_injector):
    injector = await a_injector(dependency_injection.Injector)
    ainjector = injector(dependency_injection.AsyncInjector)
    cancelled = False
    k = dependency_injection.InjectionKey("bar")

    async def func():
        nonlocal cancelled
        try:
            await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            cancelled = True
        return 39
    ainjector.add_provider(k, when_needed(func))
    loop.create_task(ainjector.get_instance_async(k))
    await asyncio.sleep(0.1)
    await dependency_injection.shutdown_injector(ainjector)
    assert cancelled is True


def test_injectable_sets_dependencies(injector):
    "Test that the constructor for Injectable tries to store dependencies as instance variables"
    k = InjectionKey("test_key")

    @inject(foo=k)
    class i1(dependency_injection.Injectable):
        pass
    injector.add_provider(k, 33)
    i1_obj = injector(i1)
    assert i1_obj.foo == 33


def test_injectable_fails_on_unknown_args(injector):
    k = InjectionKey("test_key")

    @inject(foo=k)
    class i1(dependency_injection.Injectable):
        pass
    injector.add_provider(k, 33)
    with pytest.raises(TypeError):
        i1_obj = injector(i1, bar=40)


def test_injectable_autokwargs(injector):
    k = InjectionKey("test_key")

    @inject_autokwargs(foo=k)
    class i1(dependency_injection.Injectable):
        pass
    injector.add_provider(k, 40)
    i1_obj = injector(i1)
    assert i1_obj.foo == 40
    with pytest.raises(TypeError):
        i1()


@async_test
async def test_injectable_inheritance(injector, a_injector):
    from carthage.dependency_injection import Injectable
    k1 = InjectionKey("k1")
    k2 = InjectionKey("k2")

    @inject_autokwargs(k1=k1)
    class a(Injectable):
        pass

    @inject(k2=k2)
    class b(a):
        pass

    @inject(k1=None)
    class c(b):

        def __init__(self, **kwargs):
            super().__init__(k1=20, **kwargs)

    injector.add_provider(k1, 10)
    injector.add_provider(k2, 30)
    b_obj = injector(b)
    assert b_obj.k1 == 10
    assert b_obj.k2 == 30
    c_obj = injector(c)
    assert c_obj.k2 == 30
    assert c_obj.k1 == 20

    a_injector.add_provider(k1, 30)
    a_injector.add_provider(k2, 55)
    c_obj2 = await a_injector(c)
    assert c_obj2.k1 == 20


@async_test
async def test_injector_claiming(injector, a_injector):
    ainjector = a_injector
    i2 = injector(dependency_injection.Injector)
    assert i2.claimed_by is None
    assert i2.claim() is i2
    i3 = i2.claim()
    assert i2 is not i3
    assert i3.claimed_by

    class c(AsyncInjectable):
        pass
    c_obj = await ainjector(c)
    assert c_obj.injector.claimed_by() is c_obj
    ai2 = c_obj.ainjector.claim()
    assert ai2 is not c_obj.ainjector
    assert ai2.injector is not c_obj.ainjector.injector
    assert c_obj.ainjector.injector is c_obj.injector
    # If you are permitted to override injectors on a call to an
    # injector, interesting semantic questions come up; are you just
    # overriding the kwarg, or are you also overriding what the
    # subinjector will provide when asked to provide an injector.  If
    # so, you are violating the invarient that injectors always inject
    # themselves when asked for an injector.  In any case, if it ever
    # becomes possible to override the injector keyword, the following
    # test probably should pass for any reasonable semantics.
    with pytest.raises(dependency_injection.ExistingProvider):
        c2_obj = await ainjector(c, injector=i3)
        assert c2_obj.injector.claimed_by() is c2_obj
        assert c2_obj.injector.parent_injector is i3


def test_injection_key_copy():
    i1 = InjectionKey(int)
    assert i1 is InjectionKey(i1)
    i2 = InjectionKey(i1, optional=True)
    assert i2.optional
    assert i2 == i1
    assert i2 is not i1


def test_none_kwargs():
    class foo(dependency_injection.Injectable):
        pass
    injector = dependency_injection.Injector()
    injector.add_provider(foo)
    @inject(i=foo)
    def isinst(i): return isinstance(i, foo)
    assert injector(isinst) is True
    assert injector(isinst, i=None) is False


@async_test
async def test_async_not_ready(a_injector):
    class AsyncDependency(AsyncInjectable):
        ready = False

        async def async_ready(self):
            await super().async_ready()
            self.ready = True

    @inject(baz=InjectionKey(AsyncDependency, _ready=True))
    class AsyncDependency2(AsyncDependency):
        pass
    k = InjectionKey("baz")
    k2 = InjectionKey("bazquux")
    a_injector.add_provider(k, AsyncDependency)
    a_injector.add_provider(k2, AsyncDependency2)
    nr = await a_injector.get_instance_async(InjectionKey(k, _ready=False))
    assert isinstance(nr, AsyncDependency)
    assert nr.ready is False
    nr2 = await a_injector.get_instance_async(k)
    assert nr2 is nr
    assert nr.ready is True
    nr3 = await a_injector.get_instance_async(InjectionKey(k2, _ready=False))
    assert nr3.baz.ready is True
    assert nr3.ready is False


@async_test
async def test_dependency_quote(a_injector):
    class AsyncDependency(AsyncInjectable):

        async def async_ready(self):
            raise AssertionError

    k = InjectionKey("foo")

    @inject(foo=k)
    def func(foo):
        assert foo is AsyncDependency
    await a_injector(func, foo=dependency_injection.dependency_quote(AsyncDependency))


def test_injector_xref(injector):
    injector_xref = dependency_injection.injector_xref

    class Target(Injectable):
        pass

    @inject(injector=dependency_injection.Injector)
    class Sub(Injectable):

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.injector.add_provider(InjectionKey(42), Target)
    injector.add_provider(Sub)
    injector.add_provider(InjectionKey("target"),
                          injector_xref(
                              InjectionKey(Sub),
                              InjectionKey(Target)))
    res = injector.get_instance(InjectionKey("target"))
    assert isinstance(res, Target)


def test_system_dependency_injection_keys(injector):
    "Test that SystemDependencies inject with a name"
    from carthage.system_dependency import MachineDependency, SystemDependency
    md = MachineDependency("foo.com")
    injector.add_provider(md)
    md2 = injector.get_instance(InjectionKey(SystemDependency, name="foo.com"))
    assert md is md2


@async_test
async def test_concurrent_resolution(a_injector, loop):
    "Test that if a  get_instance has already recorded a partial future, kwarg placement ends up working"
    ainjector = a_injector
    fut_inprogress = loop.create_future()
    fut_unblock = loop.create_future()

    async def block_for_a_while():
        fut_inprogress.set_result(True)
        await fut_unblock
        return 333

    key = InjectionKey("async_function")
    ainjector.add_provider(key, block_for_a_while)

    @inject(foo=key)
    async def wait_for_foo(foo):
        assert foo == 333
        return True

    fut_key = loop.create_task(ainjector.get_instance_async(key))
    # at this point we've requested key to be resolved, but probably block_for_a_while has not even started
    await fut_inprogress
    # At this point block_for_a_while is running, and that future has been recorded as the provider for key
    fut_wait_for_foo = loop.create_task(ainjector(wait_for_foo))
    # Now wait a bit so that wait_for_foo is blocking on the result of key
    # If the time becomes a problem we could probably have a second dependency
    # for wait_for_foo that sets its own future and wait for that here
    await asyncio.sleep(0.2)
    fut_unblock.set_result(True)
    assert await fut_wait_for_foo is True
    await fut_key


@async_test
async def test_async_become_ready_handles_dependencies(a_injector):
    ainjector = a_injector

    @inject(bar=InjectionKey("bar"))
    class Foo(AsyncInjectable):

        async def async_ready(self):
            assert self.bar.ready is True
            return await super().async_ready()

    class Bar(AsyncInjectable):

        ready = False

        async def async_ready(self):
            self.ready = True
            return await super().async_ready()

    ainjector.add_provider(InjectionKey("bar"), Bar)
    ainjector.add_provider(Foo)
    foo = await ainjector.get_instance_async(InjectionKey(Foo, _ready=False))
    assert foo.bar.ready is False
    await foo.async_become_ready()
    assert foo.bar.ready is True


@async_test
async def test_notready_cycles_okay(a_injector):
    ainjector = a_injector

    class Cycle(AsyncInjectable):

        async def async_ready(self):
            # If things fail this will hang
            res = await self.ainjector.filter_instantiate_async(Cycle, ['name'], ready=False)
            assert res[0][1] is self
            return await super().async_ready()

    foo_key = InjectionKey(Cycle, name="foo")
    ainjector.add_provider(foo_key, Cycle)
    await ainjector.get_instance_async(foo_key)


@async_test
async def test_introspection_registers_root(a_injector, loop):
    ainjector = a_injector

    class c(AsyncInjectable):
        async def async_ready(self):
            trigger_1.trigger()
            await trigger_2
            await super().async_ready()

    with Trigger() as trigger_1, Trigger() as trigger_2:
        assert len(instantiation_roots) == 0
        ainjector.add_provider(c)
        c_future = loop.create_task(ainjector.get_instance_async(c))
        await trigger_1
        assert len(instantiation_roots) == 1
        for ic in instantiation_roots:
            assert hasattr(ic, 'key')
            assert ic.key is InjectionKey(c)
            assert len(ic.dependencies_waiting) == 0
        trigger_2.trigger()
        await c_future
    assert len(instantiation_roots) == 0


@async_test
async def test_ready_event_propagation(a_injector, loop):
    "Make sure that when multiple instantiations of an object, one to ready and one not to ready are in progress, the right context is recorded in dependency_waiting so that we can subscribe to the right events."
    ainjector = a_injector

    class ToReady(AsyncInjectable):

        async def async_resolve(self):
            trigger_async_resolve_started.trigger()
            await trigger_async_resolve
            return self

        async def async_ready(self):
            trigger_async_ready_started.trigger()
            return await super().async_ready()

    @inject(to_ready=InjectionKey(ToReady, _ready=False))
    class Dependent1(Injectable):
        pass

    @inject(to_ready=ToReady)
    class Dependent2(Injectable):
        pass

    def dp_callback(*args, target, **kwargs):
        if target.ready:
            trigger_event_received.trigger()
    sub_injector = await ainjector(Injector)
    sub_ainjector = sub_injector(AsyncInjector)
    # we start in the super injector with something that will cause
    # ToReady to get started being instantiated.  In the sub injector
    # we pick that up as an in-progress dependency and make sure that
    # if we look up the dependency in the introspection interface and
    # subscribe to that, we get the right callback.  I.E. we make sure
    # we get the InstantiationContext waiting in the higher level
    # injector.
    with Trigger() as trigger_async_resolve_started, \
            Trigger() as trigger_async_resolve, \
            Trigger() as trigger_async_ready_started, \
            Trigger() as trigger_event_received:
        ainjector.add_provider(Dependent1)
        ainjector.add_provider(ToReady)
        assert len(instantiation_roots) == 0
        dependent1_future = loop.create_task(
            ainjector.get_instance_async(Dependent1))
        await trigger_async_resolve_started
        sub_ainjector.add_provider(Dependent2)
        dependent2_future = loop.create_task(
            sub_ainjector.get_instance_async(Dependent2))
        trigger_async_resolve.trigger()
        with ainjector.injector.event_listener_context(
                InjectionKey(ToReady), "dependency_final", dp_callback) as futures:
            await dependent2_future
        trigger_async_ready_started.assert_triggered()
        if futures:
            await asyncio.gather(futures)
        trigger_event_received.assert_triggered()
    assert len(instantiation_roots) == 0


def test_optional_not_present(injector):
    class SomeDependency(Injectable):
        pass

    @inject(dep=InjectionKey(SomeDependency, _optional=NotPresent))
    def func(**kwargs):
        assert len(kwargs) == 0
    injector(func)

    @inject_autokwargs(dep=InjectionKey("foo", _optional=NotPresent))
    class AnotherDependency(Injectable):
        pass
    instance = injector(AnotherDependency)
    assert not hasattr(instance, 'dep')
    injector.add_provider(InjectionKey('foo'), 42)
    instance2 = injector(AnotherDependency)
    assert instance2.dep == 42
@async_test
async def test_resolve_deferred(ainjector):
    foo = InjectionKey('foo')
    bar = InjectionKey('bar')
    baz = InjectionKey('baz')
    @inject(baz=baz)
    def func(baz, quux):
        return baz*quux
    args = dict(quux=10)
    ainjector.add_provider(foo, 'foo')
    ainjector.add_provider(bar, 'bar')
    ainjector.add_provider(baz, 4)
    result = await ainjector(
        resolve_deferred, ainjector, [
        90,
        dict(a=10,b=20),
        dict(c=foo,d=func),
        bar], args=args)
    assert result == [
        90,
        dict(a=10, b=20),
        dict(c='foo', d=40),
        'bar']

@async_test
async def test_filter_failure(ainjector):
    class Plugin(AsyncInjectable):

        @classmethod
        def default_class_injection_key(cls):
            return InjectionKey(Plugin, name=cls.name)

    @inject_autokwargs(not_found=InjectionKey('not_found'))
    class not_found(Plugin):
        '''This plugin fails to instantiate because it has a missing dependency
                       '''
        name = 'not_found'

    ainjector.add_provider(not_found)
    with pytest.raises(InjectionFailed):
        await ainjector.filter_instantiate_async(Plugin, ['name'], ready=True)

def test_injection_key_typos():
    '''
        Confirm that an invalid option to InjectionKey raises.
        '''
    with pytest.raises(TypeError, match='not an InjectionKey parameter'):
        InjectionKey(Injector, _foo=42)
            
