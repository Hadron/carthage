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
    

disk_sizes
    A sequence of disk sizes for primary and secondary disks.  Provided in GiB.

nested_virt
    Boolean indicating whether to allow nested virtualization

    
