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


@async_test
async def test_emit_event_fallback_running_loop(loop):
    listener = EventListener()
    callback_called = 0

    async def callback(**kwargs):
        nonlocal callback_called
        await asyncio.sleep(0)
        callback_called += 1

    listener.add_event_listener("foo", "event_1", callback)
    listener.emit_event("foo", "event_1", listener)
    futures = listener.remove_event_listener("foo", callback)
    assert futures
    await asyncio.gather(*futures)
    assert callback_called == 1


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


def test_carthage_main_register_loop(loop):
    from carthage.utils import carthage_main_register_loop
    root = Injector()
    assert root.loop is None
    key = InjectionKey(asyncio.AbstractEventLoop)
    seen = []
    root.add_event_listener(key, 'loop_ready',
                            lambda key, event, target, **kw: seen.append(target))
    root.add_event_listener(InjectionKey(Injector), 'loop_ready',
                            lambda key, event, target, **kw: seen.append(('adl', target)))
    l = carthage_main_register_loop(root)
    assert root.loop is l
    assert len(seen) == 2
    assert seen.count(l) == 1
    assert seen.count(('adl', l)) == 1
    # Idempotent: no new loop, no second event
    l2 = carthage_main_register_loop(root)
    assert l2 is l
    assert len(seen) == 2
    # Explicit loop argument with an existing registration is also a no-op
    l3 = carthage_main_register_loop(root, l)
    assert l3 is l
    assert len(seen) == 2


def test_carthage_main_register_loop_existing(loop):
    from carthage.utils import carthage_main_register_loop
    root = Injector()
    root.add_provider(InjectionKey(asyncio.AbstractEventLoop), loop, close=False)
    seen = []
    root.add_event_listener(
        InjectionKey(asyncio.AbstractEventLoop), 'loop_ready',
        lambda key, event, target, **kw: seen.append(target))
    l = carthage_main_register_loop(root)
    assert l is loop
    assert seen == []


def _fresh_injector_pair():
    from carthage.config import inject_config
    root = Injector()
    inject_config(root)
    child = root(Injector)
    return root, child


@pytest.fixture
def entanglement_module():
    # carthage.entanglement requires the entanglement package
    pytest.importorskip("entanglement")
    from carthage.entanglement import carthage_plugin, CarthageEntanglement
    return carthage_plugin, CarthageEntanglement


def test_entanglement_loop_ready_event(entanglement_module, loop):
    from carthage import ConfigLayout
    from carthage.utils import carthage_main_register_loop
    carthage_plugin, CarthageEntanglement = entanglement_module
    root, child = _fresh_injector_pair()
    child(ConfigLayout).entanglement.run_server = True
    carthage_plugin(child)
    ent = child.get_instance(CarthageEntanglement)
    assert ent.sync_server_needed is True
    assert not hasattr(ent, 'server')
    l = carthage_main_register_loop(root)
    assert ent.server is not None
    assert ent.server.loop is l


def test_entanglement_no_server_without_flag(entanglement_module, loop):
    from carthage import ConfigLayout
    from carthage.utils import carthage_main_register_loop
    carthage_plugin, CarthageEntanglement = entanglement_module
    root, child = _fresh_injector_pair()
    carthage_plugin(child)
    ent = child.get_instance(CarthageEntanglement)
    assert ent.sync_server_needed is False
    l = carthage_main_register_loop(root)
    assert not hasattr(ent, 'server')


def test_entanglement_catchup_loop_present(entanglement_module, loop):
    from carthage import ConfigLayout
    from carthage.utils import carthage_main_register_loop
    carthage_plugin, CarthageEntanglement = entanglement_module
    root, child = _fresh_injector_pair()
    child(ConfigLayout).entanglement.run_server = True
    l = carthage_main_register_loop(root)
    carthage_plugin(child)
    ent = child.get_instance(CarthageEntanglement)
    # A loop was registered before the plugin loaded; the event already
    # fired, so the server must be started at instantiation.
    assert ent.server is not None
    assert ent.server.loop is l
