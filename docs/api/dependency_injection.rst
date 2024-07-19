Dependency Injection Module
===========================
       
.. automodule:: carthage.dependency_injection
   :members:
   :show-inheritance:

.. automodule:: carthage.dependency_injection.introspection
   :members:
                  
Events
******

The dependency injection system emits several :meth:`events <carthage.event.EventListener.emit_event>`.

    add_provider
        Emitted  when :meth:`Injector.add_provider <carthage.dependency_injection.Injector.add_provider>` is called.  Dispatched to  all the keys that the dependency will satisfy.  
        The target of the event is the object providing the dependency, typically an uninstantiated class.  
        Also dispatched to ``InjectionKey(Injector)`` as a wildcard.  Contains the add_provider parameters as well as *other_keys*, indicating other keys by which this dependency will be provided.

    dependency_progress
        Emitted whenever an instantiation makes progress (for example resolving a :class:`AsyncInjectable <carthage.dependency_injection.AsyncInjectable>` or 
        calling a coroutine.  The target is a :class:`InstantiationContext <carthage.dependency_injection.introspection.InstantiationContext>`.  
        The value can be obtained with the *get_value* method.  This event is dispatched to all the keys that the *add_provider* event would be dispatched to.

    dependency_final
        Emitted whenever an instantiation finalizes (async object is ready for example). Same target and keys as *dependency_progress*.
