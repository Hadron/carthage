.. _modeling:

Modeling Layer
==============


Classes like :class:`carthage.Network` and :class:`carthage.machine.AbstractMachineModel` provide an abstract interface to infrastructure resources.

The modeling layer provides a generally declarative interface for defining and configuring such models.
The modeling layer provides a domain-specific language for describing models.  Python metaclasses are used to modify Python's behavior in a number of ways to provide a more concise language for describing models.

A Simple Model
**************

.. literalinclude:: example_1.py
    :linenos:
    :name: modeling:example_1
    :caption: A simple modeling layout to define a machine


With such a model, one might instantiate the layout by applying an injector::

   layout_instance = injector(layout)

The *layout* class is an instance of :class:`.CarthageLayout` which is a kind of :class:`InjectableModelType`.  By default each assignment of a type  in the class body of a :class:`InjectableModelType` is turned into a runtime instantiation.  This means that while ``layout.foo`` is a class (or actually a class property), ``layout_instance.foo`` is an :class:`injector_access`.  The first time ``layout_instance.foo`` is accessed, ``layout_instance.injector`` is used to instantiate it.  Thereafter, ``layout_instance.foo`` is an instance of ``layout.foo``.


The Modeling Language
*********************

Modeling classes are divided into several types (metaclasses).  Names that include the word ``modeling`` are internal.  Users may need to know about their attributes, but these classes should only be used in extending the modeling layer.  Classes containing ``model`` in their name are directly usable in layouts.
This section describes the behavior of the modeling types that make up the modeling language.

Model classes sometimes involve a new construct called a **modelmethod**.  Unlike other types of methods, modelmethods are available in the class body.  For example, *add_provider* can be used to indicate that on class instantiation, some object should be added to an :class:`InjectableModel`\ 's injector::

  class foo(InjectableModel):
      add_provider(InjectionKey("baz"), Network)


.. class:: ModelingBase
    ModelingBaseType

    All modeling classes derive their type from *ModelingBase* and have the following behaviors:

    * Unlike normal Python, an inner class can access the attributes of an outer class while the class body is being defined::

        class foo(metaclass  = ModelingBase):
            attr = 32
            b = attr+1
            class bar(metaclass = ModelingBase):
                a = b+1
                attr = 64

      In the above example, while the body of *bar* is being defined, *attr* and *b* are available.

      However, only variables that are actually set in a class body survive into the actual class.  So in the above example, ``foo.bar.a`` and ``foo.bar.attr`` are set in the resulting class.  While it was used in the class body, ``foo.bar.b`` will raise :exc:`AttributeError`.  If an attribute should be copied into an inner class, the following will work::

        class outer(metaclass = ModelingBase):
            outer_attr = []
            class inner(metaclass = ModelingBase):
                outer_attr = outer_attr

    * ModelingBases support the :ref:`modeling decorators <modeling:decorators>`.

    * The :func:`dynamic_name` decorator can be used to change the name under which an assignment is stored.  This permits programatic creation of several classes in a loop::

        class example(metaclass = ModelingBase):

            # create a machine for each user
            for u in users:
                @dynamic_name(f'{u}_workstation')
                class workstation(MachineModel): # ...
            del u #to avoid polluting class namespace Now we have
            #several workstation inner classes, named based on the
            #argument to dynamic_name rather than each being called
            #workstation.

      The dynamic_name decorator is particularly useful with :ref:`injectors <modeling:dynamic_name_injectors>` where it can be used to build up a set of machines that can be selected using :meth:`.Injector.filter_instantiate`.

.. class:: InjectableModel
    InjectableModelType

    InjectableModel represents an :class:`~carthage.dependency_injection.Injectable`.  InjectableModels  have the following attributes:

    * InjectableModels automatically have an :class:`~carthage.dependency_injection.Injector` injected and made available as the *injector* attribute.

    * By default, any attribute assigned a value in the body of the class is also added  as a provider to the injector in the class using the attribute name as a key.  That is::

        class foo(InjectableModel):
            attr = "This String"

        foo_instance = injector(foo)
        assert foo_instance.injector.get_instance(InjectionKey("attr")) == foo_instance.attr == foo.attr

      This makes it very convenient to refer to networks and to construct instances that need to be constructed in an asynchronous context.  Ideally there would be a decorator to turn this behavior off for a particular assignment, but currently there is not.

    * By default, any attribute in the class body assigned  a value that is a type (or that has a :func:`transclusion key <transclude_overrides>`) will be transformed into an :func:`injector_access`.  When accessed through the class, the *injector_access* will act as a class property returning the value originally assigned to the attribute.  That is, class access generally works as if no transformation had taken place.  However, when accessed as an instance property, the *get_instance* method on the Injector will be used to instantiate the class.  See the :ref:`first example <modeling:example_1>` for an example. If this transformation is not desired use the :func:`no_instantiate` decorator.


    * Certain classes such as :class:`carthage.network.NetworkConfig` will automatically be added  to an injector if they are  assigned to an attribute in the class body.


    * The :func:`provides` and :func:`globally_unique_key` decorators can be used to add additional :class:`InjectionKeys <InjectionKey>` by which a value can be known.

    * The :func:`allow_multiple` and :func:`no_close` decorators can modify how a value is added to the injector.

    Decorators are designed to be applied to classes or functions.  If modeling decorators need to be applied to other values the following syntax can be used::

      external_object = no_close()(object)
      val_with_extra_keys = provides(InjectionKey("an_extra_key"))(val)

    .. _modeling:dynamic_name_injectors:

    The :func:`dynamic_name` decorator is powerful when used with *InjectableModel*.  As an example, a collection of machines can be created:

      .. code-block:: python

        class machine_enclave(Enclave):

            domain = "example.com"
            for i in range(1,5):
                @dynamic_name(f'server_{i}')
                @globally_unique_key(InjectionKey(MachineModel, host = f'server-{i}.{domain}'))
                class machine(MachineModel):
                    name = f"server-{i}"

      Note that the call to :func:`globally_unique_key` is included only for illustrative purposes.  The :meth:`~MachineModel.our_key` method of :class:`MachineModel` accomplishes the same goal.

      With a layout like the above, machine models are available as ``machine_enclave.server_1``.  But once the layout is instantiated, the injector can also be used::

        machines = injector(machine_enclave)
        machines.injector.get_instance("server_1")
        #also available with the global key
        machines.injector.get_instance(InjectionKey(MachineModel, host = "server-1.example.com"))
        #Or available all at once:
        all_machines = machines.injector.filter_instantiate(MachineModel, ['host'], stop_at = machines.injector)

    .. method:: add_provider(key:InjectionKey, value, **options)

        Adds *key* to the set of keys that will be registered with an instance's injector when the model is instantiated.  Eventually, in class initialization, code similar to the following will be called::

          self.injector.add_provider(key, value, **options)

.. class:: ModelContainer
    ModelingContainer


    :class:`InjectableModel` provides downward propagation.  That is,
    names defined in outer classes are available at class definition
    time in inner classes.  Since :func:`injector_access` is used to
    instantiate inner classes, this means that the parent injector for
    the inner class is the outer class.  Thus, attributes and provided
    dependencies made available in the outer class are available in
    the inner class at runtime through the injector hierarchy.

    Sometimes upward propagation is desired.  Consider the following example:

    .. literalinclude:: example_2.py

    In this example machines can be accessed as ``layout.bank_com.server`` and ``layout.it_com.server``.  Once instantiated, the following injector access also works::

        l = injector(layout)
        l.bank_com.injector.get_instance(InjectionKey(MachineModel, host = "server.bank.com"))
        l.it_com.injector.get_instance(InjectionKey(MachineModel, host = "server.it.com"))

    But you might want to look at machines without knowing where they are defined in the hierarchy::

        l.injector.get_instance(InjectionKey(MachineModel, host = "server.it.com"))
        # Or all the machines in the entire layout
        l.injector.filter(MachineModel, ['host'], stop_at = l.injector)

Modeling containers provide upward propagation so these calls work:
    entries registered in ``l.it_com.injector`` are propagated so they
    are available in ``l.injector``.  That's the opposite direction of
    how injectors normally work.  Upward propagation is only at model
    definition time; the set of items to be propagated are collected
    statically as the class is defined.  Items added to injectors at
    runtime are not automatically propagated up.


    .. method: our_key()

        A classmethod returning a key under which this container should be registered in the parent.  If provided, the key returned by this method will be associated with the class as if it were decorated with :func:`propagate_key`.

    For upward propagation to work, containers must provide dependencies for some :class:`InjectionKey`, and that key must have some constraints associated with it.  For example, :class:`Enclave`\ 's *our_key* method provides ``InjectionKey(Enclave, domain = self.domain)``.  If keys  with constraints are marked with :func:`propagate_key`, then those are used.  If not, then all keys with constraints are used.

    When one container is added to another, all  the container propagations in the inner container are propagated to the outer container as follows:

    * If the propagation has a :func:`globally_unique_key`, then that key is registered unmodified in the outer container.

    * If there is no globally unique key, then the constraints of the propagation's key are merged with the constraints of the key under which the inner container is registered with the outer container.  Consider an inner container ``InjectionKey(Enclave, domain="it.com")`` and a propagation of ``InjectionKey(Network, role = "site"``).  Within the inner container, the network can be accessed using ``InjectionKey(Network, role = "site")``.  After the constraints are merged, the network can be accessed in the outer container as ``InjectionKey(Network, role = "site", domain = "it.com")``.

    The :func:`~carthage.dependency_injection.injector_xref` facility is used so that instantiating the key in the outer container both instantiates the inner container and the object within it.

    Only the following objects are considered for propagation:

    * Any :class:`ModelContainer` including :class:`MachineModel`, :class:`NetworkModel`, :class:`ModelGroup`, :class:`ModelContainer`, and :class:`Enclave` is propagated.

    * The :func:`propagate_key` decorator can be used to request propagation for other objects.
