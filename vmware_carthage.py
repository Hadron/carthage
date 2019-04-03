#!/usr/bin/python3

import asyncio, carthage, carthage.utils
from carthage import base_injector, inject, AsyncInjector, ConfigLayout, Injector, partial_with_dependencies
from carthage.vmware import VmFolder, Vm, VmfsDataStore, VmdkTemplate, NfsDataStore, VmTemplate, inventory, DistributedPortgroup
from carthage.machine import ssh_origin
from carthage.ssh import SshKey
from carthage.vmware.image import vm_storage_key
from carthage.hadron.vmware import CarthageVm, aces_vm_template
from carthage.config import ConfigIterator
from carthage.network import external_network_key
import carthage.vmware.network
from carthage.dependency_injection import DependencyProvider

@inject(ainjector = AsyncInjector)
async def run(ainjector):
    futures = []
    ainjector.replace_provider(ssh_origin, DependencyProvider(None))
    config = base_injector(ConfigLayout)
    if args.cleanup:
        config.tasks.dry_run = True
    await ainjector.get_instance_async(SshKey)
    template = await ainjector(aces_vm_template)
    if not args.cleanup:
        vm = await ainjector(CarthageVm, args.name, template = template)
        try: await vm.start_machine()
        except TimeoutError:
            import traceback
            traceback.print_exc()
        breakpoint()
    if args.cleanup:
        if args.cleanup_images:
            config.delete_volumes = True
        folder = await ainjector(VmFolder, config.vmware.folder)
        await folder.delete()
        for n in await ainjector(carthage.vmware.network.our_portgroups_for_switch):
            n.Destroy()
        
    if futures:
        await asyncio.wait(futures)




    
parser = carthage.utils.carthage_main_argparser()
parser.add_argument('name', help = "Name of vm", nargs ='?')
parser.add_argument('--cleanup', action = 'store_true')
parser.add_argument('--cleanup-images', action ='store_true')

args = carthage.utils.carthage_main_setup(parser)
carthage.utils.carthage_main_run(run)
