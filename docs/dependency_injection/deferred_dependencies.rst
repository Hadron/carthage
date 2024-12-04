.. _deferred_dependency:
Deferred Dependencies
=====================

When :ref:`Objects are instantiated <dependency_injection:how` Carthage typically instantiates each dependency prior to instantiating the object that the dependency is injected into.
Sometimes this is undesirable. Consider a VM that is cloned multiple times from a Windows image.

#. Generating the windows base image takes significant time.

#. Then software is installed on the base clone, taking time.

#. Then the base clone is duplicated several times.

If the Windows base image is deleted, but the resulting fully prepared clones still exist, it is not desirable to rebuild the Windows base image until the next time the clones will be updated.
In the traditional injection model, the clones would depend on the base clone, which would depend on the Windows image. So booting the  clone would require rebuilding a Windows image that would not be referenced.

A deferred dependency notes the dependency when a class is declared, but does not  instantiate the dependency until it is explicitly requested by the depending object::

    @inject(vm_image=InjectionKey(ImageVolume, _defer=True))
    class Vm(AsyncInjectable):
        ...

The *Vm* class declares a dependency on :class:`ImageVolume`. However when *Vm* is instantiated, a :class:`DeferredInjection` will be passed into *Vm.__init__* rather than an :class:`ImageVolume`.

Overriding the parameter does set up the dependency in the enclosing injector::

  result = await ainjector(Vm, vm_image=our_vm_image)

The injector set up for the *Vm* object will have ``InjectionKey(ImageVolume)`` provided by ``our_vm_image``. If *our_vm_image* is itself a :class:`DeferredInjection`, then the dependency provider will be extracted and set up in the injector.

Even if *Vm* is constructed without an injector call, :meth:`Injector.__init__` will make sure that any ``_defer=True`` dependencies it handles as kwargs are wrapped in *DeferredInjections*. If the kwarg is handled prior to reaching :meth:`Injectable.__init__`, then that code needs to handle the case that the argument is not already wrapped in a :class:`DeferredInjection`.


Using a Deferred Dependency
***************************

Prior to using *vm_image*, *Vm* must call and await ``self.vm_image.instantiate_async()``::

  async def use_vm_image(self:Vm):
      await self.vm_image.instantiate()
      print(f'Now {self.vm_image.value} is an ImageVolume')

After this call, the :attr:`value` attribute can be accessed on the :class:`DeferredInjection`.
Accessing :attr:`value` before calling :meth:`~Deferredinjection.instantiate_async()` will raise.


Alternatives to Deferred Dependencies
*************************************

Not Ready Dependencies
______________________

Consider this declaration::

  @inject(vm_image=InjectionKey(Imagevolume, _ready=False))
  class Vm(AsyncInjectable):
      ...

Here, *vm_image* is always instantiated, but *Vm* decides when it is brought to ready state by calling :meth:`AsyncInjectable.async_become_ready`. In many situations, this is a simpler alternative to deferring a dependency:

#. It is simpler because the object is set  up already.

#. More introspection operations can be performed on the dependency.

However, instantiating an object may require some of the dependencies further down the dependency chain to be brought to ready. Also, Carthage needs to figure out  exactly which object will be used to satisfy the dependency. Sometimes that operation is expensive.
If :meth:`AsyncInjectable.async_resolve` is used, Carthage does not know which object will be used to provide a dependency until *async_resolve* returns.

In contrast, deferred dependencies completely break the dependency chain until the deferred dependency is instantiated.

Fully Dynamic Dependencies
__________________________

Instead of declaring a dependency on ``InjectionKey(ImageVolume)``, *Vm* could simply instantiate an :class:`ImageVolume` when it needs one::

  result = await self.ainjector.get_instance_async(ImageVolume)

there are two disadvantages to this approach:

#. The potential dependency is not known statically so it cannot be examined by introspection UIs.

#. Subclasses of *vm* cannot change the :class:`InjectionKey` that is looked up. Callers instantiating their own instance of *Vm* need to manually adjust the injected provider rather than providing a keyword argument to :meth:`Injector.__call__` in order to override the dependency.
   
