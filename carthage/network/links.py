from __future__ import annotations
import dataclasses, typing, weakref
from .base import NetworkLink
from ..dependency_injection import *

class BondLink(NetworkLink):

    local_type = "bond"
    

class BridgeLink(NetworkLink):

    local_type  = "bridge"

    vlan_filter: typing.Optional[bool] = True

@dataclasses.dataclass(eq = False)
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
        
