from __future__ import annotations
import asyncio
import weakref
from .utils import possibly_async
class EventScope:

    '''
    Typically most objects that have event listener support never have listeners attached.  So it is desirable to separate the ability to listen for events from the data structures associated with actually doing so.  An *EventScope* is attached to a *target* when a *target* gains the first event subscription.  When an object lower in the hierarchy gains an event subscription, then :meth:`.break_at` is called to create a new *EventScope* and reparent targets lower in the tree to that new scope.
    '''

    def __init__(self, target, parent: EventScope = None):
        self.target = weakref.ref(target)
        self.listeners = {}
        self.parent = parent
        if parent:
            self.children, self.finalizers = parent.find_prune_children(target)
        else:
            self.children = {id(target): weakref.WeakSet()}
            self.finalizers = {}
        for v in self.children.values():
            for c in v:
                if c._event_scope is parent:
                    c._event_scope = self
                else:
                    c._event_scope._update_parent(parent, self)
                    
                    

    def break_at(self, target: EventListener):
        '''
        :param target:  the target at which we would like to have an :class:`EventScope`

        Returns an :class:`EventScope` that receives events for the given *target* and its children, but no parent objects.
        '''
        if self.target() is target: return self
        scope = type(self)(target, parent = self)
        target._event_scope = scope
        return scope


    def add_child(self, parent, child):
        '''Must be called for any object that has *self* as an *EventScope* and is not *self.target*.'''
        assert id(parent) in self.children
        self.children[id(parent)].add(child)
        self.children[id(child)] = weakref.WeakSet()
        fin = weakref.finalize(child, self._finalize_child, self.children, self.finalizers, id(child))
        fin.atexit = False
        self.finalizers[id(child)] = fin
        
    def find_prune_children(self, new_target):
        children = {}
        finalizers = {}
        def recurse(elt):
            for c in self.children[id(elt)]:
                recurse(c)
            children[id(elt)] =  self.children[id(elt)]
            del self.children[id(elt)]
            try: del self.finalizers[id(elt)]
            except KeyError: pass
            fin = weakref.finalize(elt, self._finalize_child, children, finalizers, id(elt))
            fin.atexit = False
            finalizers[id(elt)] = fin
        recurse(new_target)
        return children, finalizers

    @staticmethod
    def _finalize_child(children, finalizers, id):
        try: del children[id]
        except: pass
        try: del finalizers[id]
        except: pass

    def _update_parent(self, old_parent, new_parent):
        p = self.parent
        assert p is not old_parent
        while p is not None:
            if p is new_parent: return
            if p.parent is old_parent:
                p.parent = new_parent
                return
            p = p.parent
        raise ValueError('old_parent is not  in the parent chain')
    

            
                
        
    def add_listener(self, k, event, callback):
        d = self.listeners.setdefault(k,{})
        d[callback] = event

    def remove_listener(self, k, callback):
        d = self.listeners[k]
        try: del d[callback]
        except KeyError:
            # We prefer our message
            raise KeyError(f'{callback} not registered as a listener for {k}') from None

    def emit(self, loop, k, event, target, *args,
             adl_keys = set(),
             **kwargs):
        target_keys = {k }|adl_keys
        result_futures = []
        if self.parent:
            result_futures.append(self.parent.emit(
                loop, k, event,target, adl_keys = adl_keys,
                **kwargs))
        for ck in target_keys:
            try: d = self.listeners[ck]
            except KeyError: continue
            for callback, events in d.items():
                if event in events:
                    result_futures.append(
                        loop.create_task(possibly_async(callback( event=event, target = target, *args, **kwargs))))

        if result_futures:
            return asyncio.gather(*result_futures)
        else:
            future = loop.create_future()
            future.set_result(False)
            return future

class EventListener:


    '''Represents an object to which event listeners can be attached using :meth:`add_event_listener`.  Events are dispatched using :meth:`emit_event`.  Events are named by a string, and dispatched to keys, typically :class:`carthage.InjectionKey`.

'''

    def __init__(self, event_scope = None):
        super().__init__()
        if event_scope:
            self._event_scope = event_scope
            return
        er = getattr(self, '_event_scope', None)
        if isinstance(er, EventScope): return
        try:
            self._event_scope = self.parent._event_scope
            self._event_scope.add_child(self.parent, self)
            return
        except AttributeError: pass
        self._event_scope = EventScope(self)

    def add_event_listener(self,key, events, callback):
        '''
         :param key: an :class:`InjectionKey` or similar key toward which the event will be dispatched.

        :param events:  a string (or sequence of strings) indicating which event will be  dispatched.

        :param callback: A callable that will be called as::

            callback(key, event, target *event_args, **event_kwargs)

        The *callback* may be asynchronous.
        '''
        self._event_scope.break_at(self)
        if isinstance(events, str):
            events = {events}
        events = frozenset(events)
        self._event_scope.add_listener(key, events, callback)

    def remove_event_listener(self, key, callback):
        self._event_scope.remove_listener(key, callback)

    def emit_event(self, key, event, target,
                   *args,
                   adl_keys = set(),
                   loop = None,
                   **kwargs):
        if loop is None:
            try:loop = self.loop
            except: loop = asyncio.get_event_loop()
        return self._event_scope.emit(loop, key, event, target,
                               *args,
                               **kwargs,
                               adl_keys = adl_keys)
        

__all__ = 'EventListener'.split()