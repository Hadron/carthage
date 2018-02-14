# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import logging, re, weakref
from . import sh
from .dependency_injection import inject, AsyncInjectable, Injector, AsyncInjector, InjectionKey
from .config import ConfigLayout

logger = logging.getLogger('carthage.network')

_cleanup_substitutions = [
    (re.compile(r'[-_\.]'),''),
    (re.compile(r'test'),'t'),
    (re.compile(r'router'), 'rtr'),
    (re.compile(r'\..+'), ''),
]

def if_name(type_prefix, layout, intf):
    "Produce 14 character interface names for networks and hosts"
    def cleanup(str, maxlen):
        first, sep, tail = str.partition('.')
        for m, r in _cleanup_substitutions:
            first = m.sub(r, first)
        maxlen -= min(len(tail), 4)
        first = first[0:maxlen]
        if tail:
            for m, r in _cleanup_substitutions:
                tail = m.sub(r, tail)
            return first+'-'+tail[0:3]
        return first
    assert len(type_prefix) <= 3
    layout = cleanup(layout, 2)
    maxlen = 12-len(layout)-len(type_prefix)
    intf = cleanup(intf, maxlen)
    return "{}{}-{}".format(type_prefix, layout, intf)

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
        bridge_member = if_name('ci', self.config_layout.container_prefix, container_name)
        veth_name = if_name('ve', self.config_layout.container_prefix, container_name)
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
    
