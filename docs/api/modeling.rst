.. _modeling:

Modeling Layer
==============

.. py:currentmodule:: carthage.modeling
                      
Classes like :class:`carthage.Network` and :class:`carthage.machine.AbstractMachineModel` provide an abstract interface to infrastructure resources.

The modeling layer provides a generally declarative interface for defining and configuring such models.
The modeling layer provides a domain-specific language for describing models.  Python metaclasses are used to modify Python's behavior in a number of ways to provide a more concise language for describing models.

A Simple Model
**************

.. literalinclude:: modeling/example_1.py
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

.. class:: InjectableModel
    InjectableModelType

    InjectableModel represents an :class:`~carthage.dependency_injection.Injectable`.  InjectableModels  have the following attributes:

    * InjectableModels automatically have an :class:`~carthage.dependency_injection.Injector` injected and made available as the *injector* attribute.

    * By default, any attribute in the class body assigned  a value that is a type or that has a :func:`transclusion key <transclude_overrides>` will be transformed into an :func:`injector_access`.  When accessed through the class, the *injector_access* will act as a class property returning the value originally assigned to the attribute.  That is, class access generally works as if no transformation had taken place.  However, when accessed as an instance property, the *get_instance* method on the Injector will be used to instantiate the class.  See the :ref:`first example <modeling:example_1>` for an example. If this transformation is not desired use the :func:`no_instantiate` decorator.

      Currently, when accessed in a class body, the raw injector_access is used.  To get the  targeted type, use the *target* attribute.  This behavior is probably a bug and likely to change::

        class mod(InjectableModel):
            foo_net = Network
            # If we want bar_net to also be a network:
            bar_net = foo_net.target #not just foo_net

    * Certain classes such as :class:`carthage.network.NetworkConfig` will automatically be added  to an injector if they are  assigned to an attribute in the class body.

    .. method:: add_provider(key:InjectionKey, value, **options)

        Adds *key* to the set of keys that will be registered with an instance's injector when the model is instantiated.  Eventually, in class initialization, code similar to the following will be called::

          self.injector.add_provider(key, value, **options)

.. class:: ModelingContainer

Blah

Base Models
***********

.. autoclass:: NetworkModel

.. autoclass:: NetworkConfigModel

.. autoclass:: MachineModel

.. _modeling:decorators:

Decorators
**********

.. autodecorator:: no_instantiate
.. autodecorator:: transclude_overrides
                   
.. autofunction:: injector_access
              
