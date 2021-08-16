Images and Volumes
==================

.. py:currentmodule:: carthage
                      
Images are handled for :class:`containers <.container.Container>`  by :class:`.image.ContainerVolume` and VMs with :class:`.image.ImageVolume`.

.. py:module:: carthage.image

   .. autoclass:: ContainerVolume

   .. autofunc:: wrap_container_customization

   .. autoclass:: ImageVolume

.. py:module:: carthage.debian

   .. autoclass:: DebianContainerImage

   .. autofunc:: debian_container_to_vm
                 
