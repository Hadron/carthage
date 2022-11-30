# Copyright (C) 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

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
                              adl_keys={'foo'})
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


def test_multiple_scope_breaks(loop):
    def callback(*args): pass
    injector = base_injector(Injector).claim("injector")
    injector2 = injector(Injector).claim("injector2")
    injector3 = injector2(Injector).claim("injector3")
    key = InjectionKey("event")
    injector3.add_event_listener(key, "foo", callback)
    injector2.add_event_listener(key, "foo", callback)
