# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import carthage.dependency_injection

from .vm import Vm, VmFolder, VmTemplate
from .image import *
from .inventory import *
from .cluster import VmwareCluster
from .utils import wait_for_task
from .connection import VmwareConnection
from .network import DistributedPortgroup, vmware_trunk_key, DvSwitch
from .host import *
from .folder import *
from . import image

import carthage.vmware.network as network


@carthage.dependency_injection.inject(injector=carthage.Injector)
def enable_new_vmware_connection(injector):
    '''
    Indicate a point in the injector hierarchy where potentially a different connection is required.  Ifg only one connection is required in a layout this function is automatically called when the *carthage.vmware* plugin is enabled.  However, this function can be called to allow for selection of a new connection, datacenter, folder and distributed switch at some point in the injector hierarchy.
    '''
    from carthage.dependency_injection import partial_with_dependencies
    from ..utils import when_needed
    from . import inventory
    from .datacenter import VmwareDatacenter
    injector.add_provider(VmFolder)
    injector.add_provider(vmware_trunk_key, network._vmware_trunk)
    injector.add_provider(
        image.vm_datastore_key,
        partial_with_dependencies(
            image.produce_datastore_from_config,
            "vmware.datastore"))
    injector.add_provider(VmwareDatacenter)
    injector.add_provider(VmwareCluster)
    injector.add_provider(DvSwitch)
    injector.add_provider(VmwareConnection)
    injector.add_provider(
        image.image_datastore_key,
        partial_with_dependencies(
            image.produce_datastore_from_config,
            "vmware.image_datastore"))


@carthage.dependency_injection.inject(
    injector=carthage.dependency_injection.Injector)
def carthage_plugin(injector):
    from ..utils import when_needed
    from . import inventory
    from .datacenter import VmwareDatacenter
    injector.add_provider(inventory.custom_fields_key, inventory.default_custom_fields)
    injector.add_provider(DistributedPortgroup, allow_multiple=True)
    injector(enable_new_vmware_connection)
