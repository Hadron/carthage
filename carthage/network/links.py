from __future__ import annotations
import typing, weakref
from .base import NetworkLink
from ..dependency_injection import *

class BondLink(NetworkLink):

    local_type = "bond"
    
