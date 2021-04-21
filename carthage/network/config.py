import copy, dataclasses
from ipaddress import *
from ..dependency_injection import *

class L3ConfigMixin:
    _attributes = frozenset({
        'dhcp',
        'dhcp_ranges',
        'address',
        'network',
        })
    
    def __post_init__(self):
        if self.dhcp_ranges:
            for l,h in self.dhcp_ranges:
                if l > h:
                    raise ValueError(f'IN a dhcp range, the lower address {l} is not less than the upper address {h}')
                if self.network and l not in self.network:
                    raise ValueError(f'{l} is not in {self.network}')
                if self.network and h not in self.network:
                    raise ValueError(f'{h} is not in {self.network}')

    def _handle_dhcp_ranges(self, func):
        def wrapper(ranges):
            result = []
            for l,h in ranges:
                result.append((func(l), func(h)))
            return result
        return wrapper

    def merge(self, merge_from):
        '''
        Return a new instance of a Networkconfig where values from *merge_from* augment values not set in *self*.  Typical usage::

            merged_v4_config = link.v4_config.merge(network.v4_config)

'''
        res = copy.copy(self)
        for a in self._attributes:
            if getattr(res,a) is None:
                setattr(res, a, getattr(merge_from, a))
        return res
    


@dataclasses.dataclass()
class V4Config(L3ConfigMixin):

    network: IPv4Network = None
    dhcp: bool = None
    dhcp_ranges: list = None
    address: IPv4Address = None

    def __post_init__(self):
        # The following depends on iteration happening in dictionary
        # order such that network is processed before dhcp_ranges
        for k, func in dict(
                address = IPv4Address,
                network = IPv4Network,
                dhcp_ranges= self._handle_dhcp_ranges(IPv4Address)).items():
            val = getattr(self,k)
            if val is not None:
                setattr(self, k, func(val))

        super().__post_init__()
        
