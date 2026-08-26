Carthage Events
===============

Carthage objects support event listeners.  An object that has event
listener support is a :class:`carthage.event.EventListener`.  The
:class:`~carthage.dependency_injection.Injector` and
:class:`~carthage.dependency_injection.Injectable` classes are
*EventListener* objects, and events emitted on an injector propagate
upward through the injector hierarchy: a listener attached to an
injector receives events emitted on that injector or any of its
sub-injectors, but not events emitted on its parent.

A listener is attached with
:meth:`add_event_listener <carthage.event.EventListener.add_event_listener>`,
which takes an :class:`~carthage.dependency_injection.InjectionKey`, a
string or sequence of event names, and a callback.  The callback is
invoked as ``callback(key, event, target, *event_args, **event_kwargs)``
and may be a coroutine.

Events are emitted with
:meth:`emit_event <carthage.event.EventListener.emit_event>`.  Besides
the key the event is emitted toward, additional keys may be specified
with the *adl_keys* parameter.

The events documented here are core events.  Other subsystems define
and document their own events:

- The networking events (``resolved``, ``public_address``) are documented in :ref:`the networking module documentation <network-events>`.
- The dependency injection events (``add_provider``, ``dependency_progress``, ``dependency_final``, ``dependency_instantiation_failed``) are documented in :ref:`the dependency injection module documentation <injection-events>`.

Core Events
___________

loop_ready
    Emitted toward ``InjectionKey(asyncio.AbstractEventLoop)`` and
    ``InjectionKey(Injector)`` when an asyncio event loop is registered
    on the base injector by
    :meth:`carthage_main_register_loop <carthage.utils.carthage_main_register_loop>`.
    The target of the event is the loop that was registered.

    An object that needs a running event loop but is instantiated before
    one exists (for example, a plugin loaded during
    :meth:`carthage_main_setup <carthage.utils.carthage_main_setup>`) can
    attach a listener for this event and act once the loop is available.
    A listener attached after the loop is already registered will not
    receive the event; it should check ``injector.loop`` directly.
