# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, logging, re, weakref
from . import sh
from .dependency_injection import inject, AsyncInjectable, Injector, AsyncInjector, InjectionKey, Injectable
from .config import ConfigLayout
from .utils import permute_identifier

logger = logging.getLogger('carthage.network')

_cleanup_substitutions = [
    (re.compile(r'[-_\.]'),''),
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

class VethInterface(NetworkInterface):

    def __init__(self, network, ifname, bridge_member_name):
        super().__init__(network, ifname)
        self.bridge_member_name = bridge_member_name
        self.closed = False

    def close(self):
        if self.closed: return
        sh.ip('link', 'del', self.bridge_member_name)
        del self.network.interfaces[self.bridge_member_name]
        self.closed = True

    def __del__(self):
        self.close()
        
        
@inject(
    config_layout = ConfigLayout,
    injector = Injector)
class Network(AsyncInjectable):

    def __init__(self, name, *, delete_bridge = True, injector, config_layout):
        self.name = name
        self.injector = injector
        self.config_layout = config_layout
        self.delete_bridge = delete_bridge
        self.interfaces = weakref.WeakValueDictionary()
        if delete_bridge is False:
            self.bridge_name = name
        else: self.bridge_name = if_name('br', config_layout.container_prefix, name)
        self.closed = False

    async def async_ready(self):
        try:
            sh.ip('link', 'show', self.bridge_name)
        except sh.ErrorReturnCode_1:
            sh.ip('link', 'add', self.bridge_name, 'type', 'bridge')
            sh.ip("link", "set", self.bridge_name, 
                    "type", "bridge", "stp_state", "1",
                    "forward_delay", "3")
            sh.ip("link", "set", self.bridge_name, "up")
        return self

    def close(self):
        if self.closed: return
        # Copy the list because we will mutate
        for i in list(self.interfaces.values()):
            i.close()
        if self.delete_bridge:
            logger.info("Network {} bringing down {}".format(self.name, self.bridge_name))
            sh.ip('link', 'del', self.bridge_name)
            self.closed = True

        
            
    def __del__(self):
        self.close()


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
    async def resolve(self, ainjector):
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
            
