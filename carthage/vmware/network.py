# Copyright (C) 2018, 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import collections.abc
import logging

from carthage import *
from carthage.network import this_network, TechnologySpecificNetwork, BridgeNetwork

from .inventory import *
from .utils import wait_for_task
from .connection import VmwareConnection
from .folder import VmwareFolder
from .cluster import VmwareCluster

from pyVmomi import vim

__all__ = (
    'VmwareNetwork',
    'DvSwitch',
    'DistributedPortgroup',
    'our_portgroups_for_switch',
    'NetworkFolder',
    'vmware_trunk_key')


class VmwareNetworkConfig(ConfigSchema, prefix="vmware"):
    distributed_switch: str
    trunk_interface: str


class NetworkFolder(VmwareFolder, kind='network'):
    pass


class DvSwitch(VmwareSpecifiedObject):

    parent_type = NetworkFolder
    stamp_type = "dvswitch"

    def __init__(self, *args, **kwargs):
        if 'name' not in kwargs:
            config = kwargs['config_layout']
            name = config.vmware.distributed_switch
            if name is None:
                raise ValueError(f'unable to create DvSwitch without name')
            if not name.startswith('/'):
                name = f'/{config.vmware.datacenter}/network/{name}'
            kwargs['name'] = name

            kwargs['readonly'] = kwargs.get('readonly', True)
        super().__init__(*args, **kwargs)

    def _config_for(self, host, pnics):

        pspecs = []
        for pnic in pnics:
            pspecs.append(vim.dvs.HostMember.PnicSpec(pnicDevice=pnic.device))

        cs = vim.dvs.HostMember.ConfigSpec()
        cs.operation = vim.ConfigSpecOperation.add
        cs.host = host.mob
        cs.backing = vim.dvs.HostMember.PnicBacking()
        cs.backing.pnicSpec = pspecs
        return cs

    async def update_hosts(self, hostmap):

        hostconfigs = []
        for host, pnics in hostmap.items():
            cs = self._config_for(host, pnics)
            cs.operation = vim.ConfigSpecOperation.add
            for h in self.mob.config.host:
                if host.mob == h.config.host:
                    cs.operation = vim.ConfigSpecOperation.edit
            hostconfigs.append(cs)

        dvspec = vim.DistributedVirtualSwitch.ConfigSpec()
        dvspec.configVersion = self.mob.config.configVersion
        dvspec.host = hostconfigs

        task = self.mob.ReconfigureDvs_Task(dvspec)
        await wait_for_task(task)

    async def do_create(self):

        logging.debug(f'creating distributed portgroup {self.name}')

        cluster = await self.ainjector(VmwareCluster)

        hostconfigs = []

        config = vim.DistributedVirtualSwitch.ConfigSpec()
        config.name = self.name
        config.uplinkPortPolicy = vim.DistributedVirtualSwitch.NameArrayUplinkPortPolicy()
        config.uplinkPortPolicy.uplinkPortName = ['uplink0', 'uplink1']
        config.maxPorts = 8000
        config.host = hostconfigs

        create = vim.DistributedVirtualSwitch.CreateSpec()
        create.configSpec = config
        create.productInfo = vim.dvs.ProductSpec(version='6.6.0')

        task = self.parent.mob.CreateDVS_Task(create)
        await wait_for_task(task)


@inject_autokwargs(network=this_network)
class VmwareNetwork(VmwareNamedObject, TechnologySpecificNetwork):

    '''Abstract Base class representing a VmwareNetwork'''

    def __repr__(self):
        return f"<{self.__class__.__name__} for {self.network.name}: {self.vmware_path}>"


@inject(
    dvswitch=DvSwitch)
class DistributedPortgroup(VmwareNetwork):

    stamp_type = "portgroup"

    parent_type = NetworkFolder

    def __init__(self, *args, config_layout, dvswitch, **kwargs):
        self.dvswitch = dvswitch
        if 'name' not in kwargs:
            kwargs['name'] = kwargs['network'].name
            kwargs['readonly'] = kwargs.get('readonly', False)
            kwargs['parent'] = dvswitch.parent
        super().__init__(*args, **kwargs, config_layout=config_layout)

    @memoproperty
    def name(self):
        return self.network.name

    async def do_create(self):
        logging.debug(f'creating distributed portgroup {self.network.name} on VLAN {self.network.vlan_id}')
        cs = vim.dvs.DistributedVirtualPortgroup.ConfigSpec()
        cs.name = self.full_name
        cs.autoExpand = True
        cs.type = "earlyBinding"
        cs.policy = vim.dvs.VmwareDistributedVirtualSwitch.VMwarePortgroupPolicy()
        cs.policy.trafficFilterOverrideAllowed = True
        cs.policy.macManagementOverrideAllowed = True
        default = vim.dvs.VmwareDistributedVirtualSwitch.VmwarePortConfigPolicy()
        default.macManagementPolicy = vim.dvs.VmwareDistributedVirtualSwitch.MacManagementPolicy()
        default.macManagementPolicy.forgedTransmits = True
        default.macManagementPolicy.allowPromiscuous = True
        default.macManagementPolicy.macChanges = True
        learning = vim.dvs.VmwareDistributedVirtualSwitch.MacLearningPolicy()
        learning.enabled = True
        learning.allowUnicastFlooding = True
        learning.limit = 512
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
            task = self.dvswitch.mob.AddDVPortgroup_Task([cs])
        else:
            cs.configVersion = self.mob.config.configVersion
            task = self.mob.ReconfigureDVPortgroup_Task(cs)
        await wait_for_task(task)
        try:
            del self.__dict__['mob']
        except KeyError:
            pass

    @memoproperty
    def full_name(self):
        prefix = self.config_layout.container_prefix
        return f"{prefix}{self.network.name}"

    async def delete(self):
        if not self.mob:
            raise RuntimeError(f"{self} does not exist")
        task = self.mob.Destroy()
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


@inject(config=ConfigLayout, connection=VmwareConnection)
def our_portgroups_for_switch(switch=None, *, config, connection):
    '''
    :return: Yield  pyVmOmi portgroup objects that match the Carthage prefix for the switch.  The main purpose is for deleting objects.

    :param switch: Name of distributed virtual switch within the Datacenter; if None, uses the switch from the config.

    '''
    if switch is None:
        switch = config.vmware.distributed_switch
    dvs = connection.content.searchIndex.FindByInventoryPath(f"{config.vmware.datacenter}/network/{switch}")
    if dvs is None:
        raise LookupError(f"{switch} DVS not found")
    prefix = config.container_prefix
    for p in dvs.portgroup:
        if p.name.startswith(prefix):
            yield p


@when_needed
@inject(config=ConfigLayout, injector=Injector)
async def _vmware_trunk(config, injector):
    trunk_interface = config.vmware.trunk_interface
    if trunk_interface is None:
        raise ValueError("You must configure config.vmware.trunk_interface")
    # This is a bit hackish.  Our goal is to construct a BridgeNetwork
    # with the right interface name to keep track of vlan interfaces
    # so they can be deleted.
    net = injector(Network, "Vmware Trunk")
    bridge = net.ainjector.injector(BridgeNetwork, bridge_name=trunk_interface, delete_bridge=False)
    net.ainjector.add_provider(bridge)
    return net

vmware_trunk_key = InjectionKey(Network, vmware_role='trunk')
