from __future__ import annotations
import typing, weakref
from .base import NetworkLink
from ..dependency_injection import *

class BondLink(NetworkLink):

    local_type = "bond"
    

class BridgeLink(NetworkLink):

    local_type  = "bridge"

    vlan_filter: typing.Optional[bool] = True
