import asyncio, collections.abc

from carthage import *
from carthage.network import this_network, TechnologySpecificNetwork

from .inventory import *
from .utils import wait_for_task
from .connection import VmwareConnection
from .folder import VmwareFolder

from pyVmomi import vim

__all__ = ('VmwareNetwork', 'DvSwitch', 'DistributedPortgroup', 'our_portgroups_for_switch', 'NetworkFolder', 'vmware_trunk_key')
            
config_defaults.add_config({'vmware': {
    'distributed_switch': None,
    'trunk_interface': None,
    }})

@inject(**VmwareFolder.injects)
class NetworkFolder(VmwareFolder, kind='network'):

    def vmware_path_for(self, child):
        if isinstance(child, (DistributedPortgroup, DvSwitch)):
            return super().vmware_path_for(self)
        return super().vmware_path_for(child)

    pass

@inject(**VmwareSpecifiedObject.injects)
class DvSwitch(VmwareSpecifiedObject):

    def vmware_path_for(self, child):

        # Without this, the DvSwitch itself will just be
        # [Datacenter]/network.
        if child is self:
            return super().vmware_path_for(child)

        # Otherwise, the child wants to go under the 'network' folder
        # directly.  Which doesn't make much sense to me, but that's
        # how VMware does it.
        return self.provided_parent.vmware_path_for(child)

    def __init__(self, *args, config_layout, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = config_layout.vmware.distributed_switch
            kwargs['readonly'] = kwargs.get('readonly', True)
        super().__init__(*args, **kwargs, config_layout=config_layout)

@inject(**VmwareNamedObject.injects, network=this_network)
class VmwareNetwork(VmwareNamedObject, TechnologySpecificNetwork):

    '''Abstract Base class representing a VmwareNetwork'''

    injects = dict(**VmwareManagedObject.injects, network=this_network)

    def __init__(self, network, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.network = network

    def __repr__(self):
        return f"<{self.__class__.__name__} for {self.network.name}: {self.vmware_path}>"

@inject(**VmwareNetwork.injects)
class DistributedPortgroup(VmwareNetwork):

    stamp_type = "portgroup"

    def __init__(self, *args, config_layout, **kwargs):
        if 'name' not in kwargs:
            kwargs['name'] = kwargs['network'].name
            kwargs['readonly'] = kwargs.get('readonly', True)
        super().__init__(*args, **kwargs, config_layout=config_layout)

    async def find_parent(self):
        if self.provided_parent is None:
            return await self.ainjector(DvSwitch)
        return await super().find_parent()

    @memoproperty
    def name(self):
        return self.network.name

    async def do_create(self):
        # print(f'creating distributed portgroup {self.network.name} on VLAN {self.network.vlan_id}')
        cs = vim.dvs.DistributedVirtualPortgroup.ConfigSpec()
        cs.name = self.full_name
        cs.autoExpand = True
        cs.type = "earlyBinding"
        cs.policy = vim.dvs.VmwareDistributedVirtualSwitch.VMwarePortgroupPolicy()
        cs.policy.macManagementOverrideAllowed = True
        default  = vim.dvs.VmwareDistributedVirtualSwitch.VmwarePortConfigPolicy()
        default.macManagementPolicy = vim.dvs.VmwareDistributedVirtualSwitch.MacManagementPolicy()
        default.macManagementPolicy.forgedTransmits = True
        learning = vim.dvs.VmwareDistributedVirtualSwitch.MacLearningPolicy()
        learning.enabled = True
        learning.allowUnicastFlooding = True
        learning.limit = 64
        learning.limitPolicy = "DROP"
        default.macManagementPolicy.macLearningPolicy = learning
        if self.network.vlan_id:
            vlan_id = self.network.vlan_id
            if isinstance(vlan_id, collections.abc.Sequence):
                min, max = vlan_id
                nr = vim.NumericRange()
                nr.start = min
                nr.end = max
                vlan_spec = vim.dvs.VmwareDistributedVirtualSwitch.TrunkVlanSpec()
                vlan_spec.vlanId = [nr]
            else:
                vlan_spec = vim.dvs.VmwareDistributedVirtualSwitch.VlanIdSpec()
                vlan_spec.vlanId = self.network.vlan_id
            default.vlan = vlan_spec
        cs.defaultPortConfig = default
        if not self.mob:
            task = self.provided_parent.mob.AddDVPortgroup_Task([cs])
        else:
            cs.configVersion = self.mob.config.configVersion
            task = self.mob.ReconfigureDVPortgroup_Task(cs)
        await wait_for_task(task)
        try:
            del self.__dict__['inventory_object']
        except KeyError: pass

    @memoproperty
    def full_name(self):
        prefix = self.config_layout.container_prefix
        return f"{prefix}{self.network.name}"

    async def delete(self):
        if not self.mob:
            raise RuntimeError(f"{self} does not exist")
        task = self.mob.Destroy()
        loop = self.injector.get_instance(asyncio.AbstractEventLoop)
        await wait_for_task(task)
        
    async def also_accessed_by(self, others):
        for n in others:
            if isinstance(n, BridgeNetwork):
                trunk = await self._get_trunk()
                ni = trunk.expose_vlan(self.network.vlan_id)
                n.add_member(ni)

    async def _get_trunk(self):
        trunk_base = await self.ainjector.get_instance_async(vmware_trunk_key)
        return await trunk_base.access_by(BridgeNetwork)
                
@inject(config = ConfigLayout, connection = VmwareConnection)
def our_portgroups_for_switch(switch = None, *, config, connection):
    '''
    :return: Yield  pyVmOmi portgroup objects that match the Carthage prefix for the switch.  The main purpose is for deleting objects.

    :param switch: Name of distributed virtual switch within the Datacenter; if None, uses the switch from the config.

    '''
    if switch is None: switch = config.vmware.distributed_switch
    dvs = connection.content.searchIndex.FindByInventoryPath(f"{config.vmware.datacenter}/network/{switch}")
    if dvs is None:
        raise LookupError(f"{switch} DVS not found")
    prefix = config.container_prefix
    for p in dvs.portgroup:
        if p.name.startswith(prefix):
            yield p

@when_needed
@inject(config = ConfigLayout, injector = Injector)
async def _vmware_trunk(config, injector):
    trunk_interface = config.vmware.trunk_interface
    if trunk_interface is None:
        raise ValueError("You must configure config.vmware.trunk_interface")
    # This is a bit hackish.  Our goal is to construct a BridgeNetwork
    # with the right interface name to keep track of vlan interfaces
    # so they can be deleted.
    net = injector(Network, "Vmware Trunk")
    bridge = net.ainjector.injector(BridgeNetwork, bridge_name = trunk_interface, delete_bridge = False)
    net.ainjector.add_provider(bridge)
    return net

vmware_trunk_key = InjectionKey(Network, vmware_role='trunk')
