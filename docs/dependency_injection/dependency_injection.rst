Dependency Injection
====================

Often in developing IAC systems, the part of the system that needs to know something about the environment is separated from the part of the system that can make that decision.  For example:

#. Cloud resources are typically placed in some folder, region or tenancy.  The resources are defined in a :ref:`layout <Modeling>` focused on describing how to create the resources.  
   The information about where to put them is in a part of the code focused on instantiating those resources.

#. Depending on how it is being used, sometimes a layout may be instantiated on virtual machines (or containers) and sometimes on real hardware.  
   As an example in the :ref:`usecase:testing`, the entire layout may be virtualized.  However, in the :ref:`usecase:customer_build`, the same 
   layout may be partially or completely built on real hardware.  As above, the layout is focused on describing the resources and how to instantiate
   them.  The application using the layout knows what hardware will be used and where virtual components will live.

#. A layout might contain a template for building a work group.  This builds a network, router, and a series of workstations.  
   These need to be connected to the broader layout.  The template needs to know where to connect and needs to know details such as the names of 
   constructed workstations.  Other parts of the layout will instantiate the template multiple times.


How Dependency Injection Works
******************************

An object such as a function or class declares dependencies using :func:`inject <carthage.dependency_injection.inject>`\ ::

  @inject(connect_to = Network)
  def build_workstation(name, *, connect_to: Network):
    #Build a workstation called name and connect to connect_to

The *inject* decorator effectively says that the decorated object/function needs some parameter, but the direct caller is unlikely to be able to supply the value. 
An object decorated this way is said to have dependencies that need to be injected.  Such objects can be called normally::

  build_workstation(name = "ws1", connect_to = some_network)

Doing so requires the caller to provide all the dependencies.  Instead, it is more common to use a :class:`Injector <carthage.dependency_injection.Injector>` to call an object that requires dependencies::

  injector(build_workstation, name = "ws1")

The :meth:`Injector <carthage.dependency_injection.Injector.__call__>` injects (supplies values for) the dependencies.  
The *name* argument of *build_workstation* needed to be supplied by the caller, because it was not marked as an injected dependency.  
However, *connect_to* can be injected by the injector if the injector or one of its parents provides a dependency for :class:`Network <carthage.network.Network>`.  
An injector can be instantiated with such dependencies::

  injector = Injector(parent)
  injector.add_provider(some_network)

This sets up an injector which inherits dependencies from an existing injector and then adds an existing network to the injector.  Most injectors eventually inherit from :obj:`carthage.base_injector`.

Injectors and Classes
*********************

:class:`Injectable <carthage.dependency_injection.Injectable>` is a base class for  objects that need dependencies injected::

  @inject_autokwargs(this_network = Network)
  class NeedsNetwork(Injectable):

      def do_something(self):
          print(self.this_network)

The :func:`inject_autokwargs <carthage.dependency_injection.inject_autokwargs>` decorator works like *inject* except that it raises :exc:`TypeError` if the 
parameter is not specified either by a caller or an injector.  :meth:`Injectable.__init__` examines dependencies associated with the class and sets an attribute on *self* capturing any provided dependency.

Injection Keys
**************

Sometimes a class may require more than one of a given kind of object.  Often an injector may have more than one of a given type of object available to provide dependencies.  
:class:`InjectionKey <carthage.dependency_injection.InjectionKey>` combines a type with a set of named constraints to select which object is required::

  @inject_autokwargs(
      outside_network = InjectionKey(Network, role="outside"),
      inside_network = InjectionKey(Network, role = "inside"))
  class Firewall(Injectable):
      # outside_network and inside_network will both be set.

Then other code can set up an injector::

  injector.add_provider(InjectionKey(Network, role="outside"), outside_network)
  injector.add_provider(InjectionKey(Network, role="inside"), inside_network)

Although it might be more common for the outside and inside network to be set up in different injectors::

  # outer_injector already provides InjectionKey(Network, role="outside")
  # Provide a firewall for foo.com, bar.com and baz.com
  for org in ("foo.com", "bar.com", "baz.com"):
      org_injector = outside_injector(Injector)
      org_network = org_injector(Network, name = f"{org} internal network")
      org_injector.add_provider(InjectionKey(Network, role="inside"), org_network)
      org_injector.add_provider(Firewall)
      org_firewall = org_injector.get_instance(Firewall)

