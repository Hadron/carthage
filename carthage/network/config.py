# Copyright (C) 2018, 2019, 2021, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import collections.abc
import copy
import dataclasses
import typing
from ipaddress import *
from ..dependency_injection import *


@dataclasses.dataclass()
class L3ConfigMixin:
    _attributes = frozenset({
        'dhcp',
        'dhcp_ranges',
        'address',
        'public_address',
        'network',
        'gateway',
        'secondary_addresses',
        "dns_servers", "domains",
    })

    #: Set of DNS servers that should be made available to this link/network
    dns_servers: list = None
    domains: str = None

    async def resolve(self, ainjector, interface):
        args = dict(interface=interface)
        for a in self._attributes:
            if getattr(self,a, None) is not None:
                setattr(self, a,
                        await resolve_deferred(ainjector, getattr(self, a), args))
        self.after_resolve()

    def after_resolve(self):

        def validate_network(field, address, self=self):
            if self.network and address not in self.network:
                raise ValueError(f"address {address} for field '{field}' is not contained in network {self.network}")

        for field in ('address', 'gateway', 'public_address'):
            if getattr(self, field):
                validate_network(field, getattr(self, field))

        if self.pool:
            l, h = self.pool
            validate_network('pool[0]', l)
            validate_network('pool[1]', h)
            if not (l < h):
                raise ValueError(f"pool address {l} is not lower than address {h}")

        for n, x in enumerate(self.dhcp_ranges or []):
            l, h = x
            validate_network(f'dhcp_ranges[{n}][0]', l)
            validate_network(f'dhcp_ranges[{n}][1]', h)
            if not (l < h):
                raise ValueError(f"dhcp_ranges[{n}] address {l} is not lower than address {h}")

        for n, x in enumerate(self.secondary_addresses or []):
            validate_network(f'secondary_addresses[{n}].private', x.private)

    def _handle_dhcp_ranges(self, func):
        def wrapper(ranges):
            result = []
            for l, h in ranges:
                result.append((func(l), func(h)))
            return result
        return wrapper

    def _handle_secondary_addresses(self, func):
        def func_or_none(a):
            if a is None: return None
            if hasattr(a, 'ip_address'): a = a.ip_address
            return func(a)
        def wrapper(l):
            result = []
            for a in l:
                if not isinstance(a, (SecondaryAddress, dict)):
                    elt = SecondaryAddress(private=func(a))
                elif isinstance(a, dict):
                    elt = SecondaryAddress(
                        public=func_or_none(a.get('public')),
                        private=func_or_none(a.get('private')),
                    )
                elif isinstance(a, SecondaryAddress):
                    elt = a
                else: raise TypeError('Don\'t know how to handle secondary address'+repr(a))
                result.append(elt)
            return result
        return wrapper

    
    def _handle_pool(self, func):
        def wrapper(pool):
            assert isinstance(pool, collections.abc.Sequence)
            assert len(pool) == 2, "Format of pool is (low, high)"
            low, high = func(pool[0]), func(pool[1])
            return low, high
        return wrapper
    
    def merge(self, merge_from):
        '''
        Return a new instance of a Networkconfig where values from *merge_from* augment values not set in *self*.  Typical usage::

            merged_v4_config = link.v4_config.merge(network.v4_config)

'''
        res = copy.copy(self)
        if merge_from is None:
            return res
        for a in self._attributes:
            if getattr(res, a) is None:
                setattr(res, a, getattr(merge_from, a))
        return res

@dataclasses.dataclass()
class SecondaryAddress:
    _address = typing.Union[IPv4Address, IPv6Address, None]
    private: _address
    public: _address = None
    del _address
    

@dataclasses.dataclass()
class V4Config(L3ConfigMixin):

    network: IPv4Network = None
    dhcp: bool = None
    dhcp_ranges: list = None
    secondary_addresses: list[IPv4Address] = dataclasses.field(default_factory=lambda: [])
    address: IPv4Address = None
    gateway: IPv4Address = None
    masquerade: bool = False
    #: Takes a lower bound and a upper bound, both specified as V4 addresses.  If specified and address is None, will assign the address between the lower and upper bound.  This allows addresses to be dynamically managed at modeling time rather than by DHCP at run time.  DHCP can still be used, but at least for models whose config includes *pool*, addresses will be statically configured in the dhcp server.
    pool: tuple = dataclasses.field(default=None, repr=False)
    public_address: IPv4Address = dataclasses.field(default=None, repr=False)
    
    _attributes = L3ConfigMixin._attributes | {'masquerade', 'pool'}

    def after_resolve(self):
        # Support things like a VpcAddress being assigned to public_address
        if hasattr(self.public_address, 'ip_address'):
            self.public_address = self.public_address.ip_address
        # The following depends on iteration happening in dictionary
        # order such that network is processed before dhcp_ranges
        for k, func in dict(
                address=IPv4Address,
                network=IPv4Network,
                gateway=ipv4_gateway,
                dhcp_ranges=self._handle_dhcp_ranges(IPv4Address),
                secondary_addresses=self._handle_secondary_addresses(IPv4Address),
                pool = self._handle_pool(IPv4Address),
                public_address=ipv4_gateway,
        ).items():
            val = getattr(self, k)
            if val is not None:
                setattr(self, k, func(val))

        super().after_resolve()


def ipv4_gateway(g):
    if g is False:
        return False
    return IPv4Address(g)
