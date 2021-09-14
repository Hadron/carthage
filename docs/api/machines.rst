Machines: Systems under Control or Simulation
=============================================

.. automodule:: carthage.machine
   :members:
      
Containers
__________

.. autoclass:: carthage.container.Container

VMs
___

.. autoclass:: carthage.vm.VM
	       
Hardware Configuration
______________________

VMs and cloud instances will look for the following properties in a :class:`AbstractMachineModel` to configure hardware:

cpus
    The number of CPUs on the virtual machine

memory_mb
    The amount of memory in megabytes

disk_size
    The size of the primary disk in bytes.  This is provided in bytes rather than KiB to allow for exact matching of image sizes.
    
nested_virt
    Boolean indicating whether to allow nested virtualization

    
