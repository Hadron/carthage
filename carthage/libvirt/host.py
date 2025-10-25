# Copyright (C) 2025 Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import logging
logger = logging.getLogger("carthage.libvirt.host")

from carthage.dependency_injection import *
from carthage.machine import Machine
from carthage.modeling.base import MachineModel
from carthage.setup_tasks import *
from carthage.utils import memoproperty, when_needed

from .base import libvirt_host_key


__all__ = []

class LibvirtHost(MachineModel, template=True):
    """A libvirt host
    """

    hypervisor_backend = "qemu"

    @classmethod
    def supplementary_injection_keys(cls, k):
        yield InjectionKey(LibvirtHost, host=cls.name)
        yield from super().supplementary_injection_keys(k)

    async def virsh(self, *args, _bg=True, _bg_exc=True, **kwargs):
        """Run a virsh command on the host
        """
        raise NotImplementedError

    @memoproperty
    def connection_string(self):
        """Override this method to provide the connection string for this host.
        """
        raise NotImplementedError
__all__ += ["LibvirtHost"]

class RemoteLibvirtHost(LibvirtHost, template=True):
    """A remote libvirt host
    """

    @classmethod
    def supplementary_injection_keys(cls, k):
        yield InjectionKey(RemoteLibvirtHost, host=cls.name)
        yield from super().supplementary_injection_keys(k)

    @memoproperty
    def connection_string(self):
        # for now we only consider ssh, not sshfs sockets or tls
        return f"{self.hypervisor_backend}+ssh://{self.model.ip_address}/system"

__all__ += ["RemoteLibvirtHost"]

class LocalLibvirtHost(LibvirtHost, template=True):
    """A local libvirt host, where Carthage is running
    """

    @classmethod
    def supplementary_injection_keys(cls, k):
        yield InjectionKey(LocalLibvirtHost, host=cls.name)
        yield from super().supplementary_injection_keys(k)

    @memoproperty
    def connection_string(self):
        return f"{self.hypervisor_backend}:///system"

__all__ += ["LocalLibvirtHost"]
