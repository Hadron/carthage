# Copyright (C) 2021, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import dataclasses
import typing
import weakref
from ipaddress import IPv4Address
from .base import NetworkLink, Network
from ..dependency_injection import *


class GRELink(NetworkLink):
    local: typing.Union[str,IPv4Address] = dataclasses.field(kw_only=True)
    remote: typing.Union[str,IPv4Address] = dataclasses.field(kw_only=True)
    key: str = None
    local_type = "gre"
    required  = False
    routes: list[Network] = dataclasses.field(default_factory=lambda: [])

class BondLink(NetworkLink):

    local_type = "bond"


class BridgeLink(NetworkLink):

    local_type = "bridge"

    vlan_filter: typing.Optional[bool] = False


@dataclasses.dataclass(eq=False)
class VlanLink(NetworkLink):

    local_type = "vlan"
    vlan_id: typing.Optional[int] = None

    @classmethod
    def validate_subclass(cls, args, unresolved):
        if not unresolved:
            if 'vlan_id' not in args and (not args['net'].vlan_id):
                raise ValueError("vlan_id must be specified directly or on the network")
        return super().validate_subclass(args, unresolved)

    def __init__(self, connection, interface, args):
        if 'vlan_id' not in args:
            args['vlan_id'] = args['net'].vlan_id
        super().__init__(connection, interface, args)


class NoLocalLink(NetworkLink):
    local_type = 'none'


@dataclasses.dataclass(init=False, eq=False)
class XfrmLocalLink(NetworkLink):

    '''A link corresponding to a linux xfrm interface'''
    interface_id: typing.Optional[int] = None
    local_type = 'xfrm'
