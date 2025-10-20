# Copyright (C) 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import deployment
from carthage.config import ConfigSchema, ConfigLayout
from carthage.dependency_injection import inject, Injector

from .base import *

class LibvirtSchema(ConfigSchema, prefix='libvirt'):

    #: The preferred format for newly created disk images
    preferred_format: str = 'raw'

    #: When creating a format like qcow2 that can be represented as a
    #delta on top of another file, should we use such a backing
    #file. If true, then that file must remain unmodified. Generally
    #it is better to use OS-level facilities like reflinks to obtain
    #copy-on-write.
    use_backing_file: bool = False

class LibvirtDeployableFinder(deployment.DeployableFinder):

    name = 'libvirt'

    async def find(self, ainjector):
        '''
        MachineDeployableFinder already finds Vms.
        '''
        return []

    async def find_orphans(self, deployables):
        try:
            import libvirt
            import carthage.modeling
        except ImportError:
            logger.debug('Not looking for libvirt orphans because libvirt API is not available')
            return []
        con = libvirt.open('')
        vm_names = [v.full_name for v in deployables if isinstance(v, Vm)]
        try:
            layout = await self.ainjector.get_instance_async(carthage.modeling.CarthageLayout)
            layout_name = layout.layout_name
        except KeyError:
            layout_name = None
        if layout_name is None:
            logger.info('Unable to find libvirt orphans because layout name not set')
            return []
        results = []
        for d in con.listAllDomains():
            try:
                metadata_str = d.metadata(libvirt.VIR_DOMAIN_METADATA_ELEMENT, 'https://github.com/hadron/carthage')
            except libvirt.libvirtError: continue
            metadata = xml.etree.ElementTree.fromstring(metadata_str)
            if metadata.attrib['layout'] != layout_name: continue
            if d.name() in vm_names:
                continue
            with instantiation_not_ready():
                vm = await self.ainjector(
                    Vm,
                    name=d.name(),
                    image=None,
                    )
                vm.injector.add_provider(deployment.orphan_policy, deployment.DeletionPolicy[metadata.attrib['orphan_policy']])
            if await vm.find():
                results.append(vm)
        return results

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.injector.add_provider(ConfigLayout)
        cl = self.injector.get_instance(ConfigLayout)
        cl.container_prefix = ""

@inject(injector=Injector)
def carthage_plugin(injector):
    injector.add_provider(LibvirtDeployableFinder)

