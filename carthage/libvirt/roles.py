# Copyright (C) 2025 Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.dependency_injection import dependency_quote
from carthage.machine import BareMetalMachine, MachineCustomization
from carthage.modeling import *
from carthage.setup_tasks import setup_task
from carthage.systemd import SystemdNetworkModelMixin

from .base import vm_image_key, Vm


__all__ = []

class LibvirtHostRole(MachineModel, template=True):
    #: Assumes debian
    class LibvirtHostRoleCustomization(MachineCustomization):
        @setup_task("Install Libvirt Host")
        async def install_libvirt_server(self):
            await self.run_command("sh", "-c", "apt update && apt install -y libvirt-daemon-system ovmf qemu-system-x86 qemu-system-modules-spice swtpm swtpm-tools")

        @setup_task("Select nftables for firewall backend")
        async def set_firewall_backend(self):
            async with self.filesystem_access() as fs:
                fp = (fs/"etc/libvirt/network.conf")
                fp.write_text("firewall_backend = \"nftables\"\n")
    
        @setup_task("Enable nested virtualization")
        async def enable_nested_virtualization(self):
            async with self.filesystem_access() as fs:
                fp = (fs/"etc/modprobe.d/10-kvm_intel.conf")
                fp.touch(mode=0o644)
                fp.write_text("options kvm_intel nested=y\n")
__all__ += ["LibvirtHostRole"]

class BaseLinuxVm(MachineModel, SystemdNetworkModelMixin, template=True):
    add_provider(machine_implementation_key, dependency_quote(Vm))
    nested_virt = False

class MicroLinuxVm(BaseLinuxVm, template=True):
    cpus = 1
    memory_mb = 2*1024
    disk_sizes = (8,)
__all__ += ["MicroLinuxVm"]

class SmallLinuxVm(BaseLinuxVm, template=True):
    cpus = 2
    memory_mb = 4*1024
    disk_sizes = (16,)
__all__ += ["SmallLinuxVm"]

class MediumLinuxVm(BaseLinuxVm, template=True):
    cpus = 2
    memory_mb = 8*1024
    disk_sizes = (32,)
__all__ += ["MediumLinuxVm"]

class LargeLinuxVm(BaseLinuxVm, template=True):
    cpus = 8
    memory_mb = 16*1024
    disk_sizes = (64,)
__all__ += ["LargeLinuxVm"]

class XLargeLinuxVm(BaseLinuxVm, template=True):
    cpus = 12
    memory_mb = 32*1024
    disk_sizes = (128,)
__all__ += ["LargeLinuxVm"]

class MachineRecord(MachineModel, template=True):
    """A machine we want to annotate for DHCP/DNS purposes
    """
    add_provider(machine_implementation_key, dependency_quote(BareMetalMachine))
    readonly = True
__all__ += ["MachineRecord"]

class LinuxMachine(MachineModel, SystemdNetworkModelMixin, template=True):
    """A linux machine networked with systemd-networkd
    """
    add_provider(machine_implementation_key, dependency_quote(BareMetalMachine))
__all__ += ["LinuxMachine"]
