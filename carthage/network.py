# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import asyncio, dataclasses, logging, re, typing, weakref
from . import sh
from .dependency_injection import inject, AsyncInjectable, Injector, AsyncInjector, InjectionKey, Injectable
from .config import ConfigLayout
from .utils import permute_identifier, when_needed, memoproperty

logger = logging.getLogger('carthage.network')

_cleanup_substitutions = [
    (re.compile(r'[-_\. ]'),''),
    (re.compile( r'database'), 'db'),
    (re.compile(r'test'),'t'),
    (re.compile(r'router'), 'rtr'),
    (re.compile(r'\..+'), ''),
]

_allocated_interfaces = set()

def if_name(type_prefix, layout, net, host = ""):
    "Produce 14 character interface names for networks and hosts"
    global _allocated_interfaces
    def cleanup(s, maxlen):
        for m, r in _cleanup_substitutions:
            s = m.sub(r, s)
        return s[0:maxlen]

    assert len(type_prefix) <= 3
    layout = cleanup(layout, 2)
    maxlen = 13-len(layout)-len(type_prefix)
    net = cleanup(net, max(3, maxlen-len(host)))
    maxlen -= len(net)
    host = cleanup(host, maxlen)
    if host: host += "-"
    id = "{t}{l}{h}{n}".format(
        t = type_prefix,
        l = layout,
        n = net,
        h = host)
    for i in permute_identifier(id, 14):
        if i not in _allocated_interfaces:
            _allocated_interfaces.add(i)
            return i
    assert False # never should be reached
    

class NetworkInterface:

    def __init__(self, network, ifname):
        self.ifname = ifname
        self.network = network

class VlanInterface(NetworkInterface):

    def __init__(self, id, network: BridgeNetwork):
        super().__init__(ifname = "{}.{}".format(
            network.bridge_name, id), network = network)
        self.vlan_id = id
        self.closed = False

    def close(self):
        if self.closed: return
        sh.ip("link", "del", self.ifname)
        self.closed = True

    def __del__(self):
        self.close()
        

        
class VethInterface(NetworkInterface):

    def __init__(self, network:BridgeNetwork, ifname, bridge_member_name):
        super().__init__(network, ifname)
        self.bridge_member_name = bridge_member_name
        self.closed = False

    def close(self):
        if self.closed: return
        try: sh.ip('link', 'del', self.bridge_member_name)
        except sh.ErrorReturnCode: pass
        del self.network.interfaces[self.bridge_member_name]
        self.closed = True

    def __del__(self):
        self.close()

class TechnologySpecificNetwork(AsyncInjectable):

    '''
    Abstract base class  for accessing a network.

    The :class:`.Network` class defines the interface to a virtual network.  However different backends require different ways of accessing a network.  For KVM we need a local bridge or macvlan interfaces.  Vmware needs some form of Portgroup on a VLAN.  This class is the abstract interface to that.

'''

    async def also_accessed_by(self, others: typing.List[TechnologySpecificNetwork]):
        '''
        Abstract method to notify a class of other technology specific networks.

        After construction, if any other technologies are in use, this method is called listing all of those technologies.  Later, if other technologies are added, this method is called again.

'''
        pass
    
                               


class Network(AsyncInjectable):

    '''
    Represents a network that VMs and containers can connect to.  In Carthage, networks are identified by a name and a VLAN identifier.  

    How networks are accessed depends on the underlying technology.  The base Network class maintains an `.Injector` so that only one instance of a technology-specific network is made for each logical network.

    .. seealso:

        BridgeNetwork
            For `carthage.Container` and `carthage.Vm`

        VmwareNetwork
            for `carthage.vmware.Vm`

    '''
    

    def __init__(self, name, vlan_id = None, **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.vlan_id = vlan_id
        self.injector.add_provider(this_network, self)
        self.technology_networks = []
        

    async def access_by(self, cls: TechnologySpecificNetwork):
        '''Request a view of *self* using *cls* as a technology-specific lens.
        
        :return: The instance of *cls* for accessing this network
        '''
        assert issubclass(cls, TechnologySpecificNetwork), \
            "Must request access by a subclass of TechnologySpecificNetwork"
        instance = None
        if (cls not in self.ainjector) and self.vlan_id is not None:
            try:
                instance = await self.ainjector.get_instance_async(InjectionKey(cls, vlan_id = self.vlan_id))
                self.ainjector.add_provider(instance)
            except KeyError: pass
        if not instance: 
            instance = await self.ainjector.get_instance_async(cls)
        assert cls in self.ainjector, \
            f"It looks like {cls} was not registered with add_provider with allow_multiple set to True"
        if instance not in self.technology_networks:
            await instance.also_accessed_by(list(self.technology_networks))
            l = [instance]
            for elt in self.technology_networks:
                await elt.also_accessed_by(l)
            self.technology_networks.extend(l)
        return instance

    def close(self, canceled_futures = None):
        self.ainjector.close(canceled_futures = canceled_futures)
        self.technology_networks = []

this_network = InjectionKey(Network, role = "this_network")

        
@inject(
    config_layout = ConfigLayout,
    injector = Injector,
    net = this_network)
class BridgeNetwork(TechnologySpecificNetwork):

    def __init__(self, net, *, bridge_name = None,
                 delete_bridge = True, **kwargs):
        super().__init__(**kwargs)
        self.name = net.name
        self.delete_bridge = delete_bridge
        self.interfaces = weakref.WeakValueDictionary()
        if bridge_name is None:
            self.bridge_name = if_name('br', self.config_layout.container_prefix, self.name)
        else: self.bridge_name = bridge_name
        self.closed = False
        self.members = []

    async def async_ready(self):
        try:
            sh.ip('link', 'show', self.bridge_name)
        except sh.ErrorReturnCode_1:
            sh.ip('link', 'add', self.bridge_name, 'type', 'bridge')
            sh.ip("link", "set", self.bridge_name, 
                    "type", "bridge", "stp_state", "1",
                    "forward_delay", "3")
            sh.ip("link", "set", self.bridge_name, "up")
        return await super().async_ready()

    def close(self):
        if self.closed: return
        self.members.clear()
        # Copy the list because we will mutate
        for i in list(self.interfaces.values()):
            try: i.close()
            except:
                logger.debug("Error deleting interface {}".format(i))
        if self.delete_bridge:
            logger.info("Network {} bringing down {}".format(self.name, self.bridge_name))
            sh.ip('link', 'del', self.bridge_name)
            self.closed = True

        
            
    def __del__(self):
        self.close()


    def add_member(self, interface):
        sh.ip("link", "set",
              interface.ifname,
              "master", self.bridge_name, "up")
        # We also keep a reference so that if it is a weak interface off another object it is not GC'd
        self.members.append(interface)
        
    def add_veth(self, container_name):
        bridge_member = if_name('ci', self.config_layout.container_prefix, self.name, container_name)
        veth_name = if_name('ve', self.config_layout.container_prefix, self.name, container_name)
        logger.debug('Network {} creating virtual ethernet for {}'.format(self.name, container_name))
        try:
            sh.ip('link', 'add', 'dev', bridge_member,
              'type', 'veth', 'peer', 'name', veth_name)
        except sh.ErrorReturnCode_2:
            logger.warn("Network {}: {} appears to exist; deleting".format(self.name, bridge_member))
            sh.ip('link', 'del', bridge_member)
            sh.ip('link', 'add', 'dev', bridge_member,
              'type', 'veth', 'peer', 'name', veth_name)
        sh.ip('link', 'set', bridge_member, 'master', self.bridge_name, 'up')
        ve = VethInterface(self, veth_name, bridge_member)
        self.interfaces[bridge_member] = ve
        return ve

    def expose_vlan(self, id):
        iface =  VlanInterface(id, self)
        ifname = iface.ifname
        try:
            sh.ip("link", "add",
                  "link", self.bridge_name,
                  "name", ifname,
                  "type", "vlan",
                  "id", id)
        except sh.ErrorReturnCode_2:
            logger.warn("{} appears to already exist".format(ifname))
        self.interfaces[ifname] = iface
        return iface

class NetworkConfig:

    '''Represents a network configuration for a container or a VM.  A
    network config maps interface names to a network and a MAC
    address.  Eventually a MAC is represented as a string and a
    network as a Network object.  However indirection is possible in
    two ways.  First, an injection key can be passed in; this
    dependency will be resolved in the context of an
    environment-specific injector.  Secondly, a callable can be passed
    in.  This callable will be called in the context of an injector
    and is expected to return the appropriate object.

    '''

    def __init__(self):
        self.nets = {}
        self.macs = {}

    def add(self, interface, net, mac):
        assert isinstance(interface, str)
        assert isinstance(mac, (str, InjectionKey, type(None))) or callable(mac)
        assert isinstance(net, (Network, InjectionKey)) or callable(net)
        self.nets[interface] = net
        self.macs[interface] = mac

    @inject(ainjector = AsyncInjector)
    async def resolve(self, access_class, ainjector):
        "Return a NetworkConfigInstance for a given environment"
        async def resolve1(r, i):
            if isinstance(r, InjectionKey):
                r = await ainjector.get_instance_async(r)
            elif  callable(r):
                r = await ainjector(r, i)
            return r
        d = {}
        for i in self.nets:
            #Unpacking assignment to get parallel resolution of futures
            res = {}
            res['mac'], res['net'] = await asyncio.gather(
                resolve1(self.macs[i], i),
                resolve1(self.nets[i], i))
            assert isinstance(res['mac'], (str, type(None))), "MAC Address for {} must resolve to string or None".format(i)
            assert isinstance(res['net'], Network), "Network for {} must resolve to network object".format(i)
            res['net'] = await res['net'].access_by(access_class)
            d[i] = res
        return await ainjector(NetworkConfigInstance,d)

@inject(config_layout = ConfigLayout)
class NetworkConfigInstance(Injectable):

    def __init__(self, entries, config_layout):
        self.config_layout = config_layout
        self.entries = entries

    def __iter__(self):
        '''Return net, interface, MAC tuples.  Note that the caller is
        responsible for making the interface names line up correctly given the
        technology in question.
        '''

        for i,v in self.entries.items():
            yield v['net'], i, v['mac']

external_network_key = InjectionKey(Network, role = "external")

@inject(config_layout = ConfigLayout,
        injector = Injector)
class ExternalNetwork(Network):

    def __init__(self, config_layout, injector):
        vlan_id = config_layout.external_vlan_id
        kwargs = {}
        if vlan_id:
            kwargs['vlan_id'] = vlan_id
        super().__init__(name = "external network", injector = injector,
                         **kwargs)
        self.ainjector.add_provider(InjectionKey(BridgeNetwork),
                                   when_needed(BridgeNetwork, bridge_name = "brint", delete_bridge = False))

    @classmethod
    def supplementary_injection_keys(cls, k):
        yield external_network_key
        yield from super().supplementary_injection_keys(k)
        
external_network = when_needed(ExternalNetwork)

@dataclasses.dataclass
class HostMapEntry:

    ip: str
    mac: str = None

host_map_key = InjectionKey('host_map')


@inject(host_map = host_map_key,ainjector = AsyncInjector)
def mac_from_host_map(i, host_map, ainjector):
    from .machine import Machine
    machine = ainjector.get_instance(InjectionKey(Machine, ready = False))
    entry = host_map[machine.name]
    machine.ip_address = entry.ip
    return entry.mac




__all__ = r'''Network TechnologySpecificNetwork BridgeNetwork 
    external_network_key HostMapEntry mac_from_host_map host_map_key
    '''.split()
