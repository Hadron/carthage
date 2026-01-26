# Copyright (C) 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import logging
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
    listener.emit_event("foo", "event_1", listener)
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
    listener.emit_event("bar", "event_1", listener,
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
    injector2.emit_event(key, "foo", injector2)
    assert callback_called == 1
    callback_called = 0
    injector2.add_event_listener(key, "bar", callback)
    injector2.emit_event(key, "foo", injector2)
    assert callback_called == 1


def test_multiple_scope_breaks(loop):
    def callback(*args): pass
    injector = base_injector(Injector).claim("injector")
    injector2 = injector(Injector).claim("injector2")
    injector3 = injector2(Injector).claim("injector3")
    key = InjectionKey("event")
    injector3.add_event_listener(key, "foo", callback)
    injector2.add_event_listener(key, "foo", callback)


@async_test
async def test_emit_event_async_results_order(loop):
    listener = EventListener()

    def cb1(**kwargs):
        return "sync1"

    async def cb2(**kwargs):
        await asyncio.sleep(0)
        return "async2"

    def cb3(**kwargs):
        return "sync3"

    listener.add_event_listener("foo", "event_1", cb1)
    listener.add_event_listener("foo", "event_1", cb2)
    listener.add_event_listener("foo", "event_1", cb3)

    results = await listener.emit_event_async("foo", "event_1", listener)
    assert results == ["sync1", "async2", "sync3"]


def test_emit_event_sync_no_loop():
    listener = EventListener()
    callback_called = 0

    def callback(**kwargs):
        nonlocal callback_called
        callback_called += 1

    listener.add_event_listener("foo", "event_1", callback)
    listener.emit_event("foo", "event_1", listener, loop=None)
    assert callback_called == 1


def test_emit_event_skips_async_without_loop(caplog):
    listener = EventListener()
    callback_called = 0

    async def callback(**kwargs):
        nonlocal callback_called
        callback_called += 1

    listener.add_event_listener("foo", "event_1", callback)
    with caplog.at_level(logging.WARNING, logger="carthage.event"):
        listener.emit_event("foo", "event_1", listener, loop=None)
    assert callback_called == 0
    assert any("Skipping async event callback" in record.message for record in caplog.records)


def test_emit_event_logs_callback_exception(caplog):
    listener = EventListener()

    def callback(**kwargs):
        raise ValueError("boom")

    listener.add_event_listener("foo", "event_1", callback)
    with caplog.at_level(logging.ERROR, logger="carthage.event"):
        listener.emit_event("foo", "event_1", listener)
    assert any("Event callback failed" in record.message for record in caplog.records)
