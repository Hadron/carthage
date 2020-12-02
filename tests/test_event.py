import pytest
from carthage.event import EventListener
from carthage.pytest import *
from carthage.dependency_injection import *
from carthage import base_injector

@async_test
async def test_event_register(loop):
    listener = EventListener()
    callback_called = 0
    def callback(*args, **kwargs):
        nonlocal callback_called
        callback_called = 1
    listener.add_event_listener("foo", "event_1", callback)
    listener.loop = loop
    await listener.emit_event("foo", "event_1", listener)
    assert callback_called == 1
    
@async_test
async def test_event_adl_keys(loop):
    listener = EventListener()
    callback_called = 0
    def callback(*args, **kwargs):
        nonlocal callback_called
        callback_called = 1
    listener.add_event_listener("foo", "event_1", callback)
    listener.loop = loop
    await listener.emit_event("bar", "event_1", listener,
                              adl_keys = {'foo'})
    assert callback_called == 1
    
@async_test
async def test_event_scoping(loop):
    injector = base_injector(Injector)
    injector2 = injector(Injector)
    ainjector = injector2(AsyncInjector)
    key = InjectionKey("baz")
    callback_called = 0
    def callback(**kwargs):
        nonlocal callback_called
        callback_called = 1
    injector.add_event_listener(key, "foo", callback)
    await injector2.emit_event(key, "foo", injector2)
    assert callback_called == 1
    callback_called = 0
    injector2.add_event_listener(key, "bar", callback)
    await injector2.emit_event(key, "foo", injector2)
    assert callback_called == 1
    
    
