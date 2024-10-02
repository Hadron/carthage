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

.. _disk_config:

disk_config
    A sequence of dicts configuring primary and secondary disks.  The only key defined at this level is *size*, the size of the disk in GiB.  If *disk_config* is provided, *disk_sizes* is ignored.  The intent of *disk_config* is to permit MachineImplementation specific configuration of disks.  Consult the specific machine implementations for details.

nested_virt
    Boolean indicating whether to allow nested virtualization

    
hardware_tpm
    Whether to provide a TPM in the virtual machine.

console_needed
    Whether a graphical console is needed.
