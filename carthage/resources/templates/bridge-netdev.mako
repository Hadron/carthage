[NetDev]
Name=${link.interface}
Kind=bridge
%if link.mac:
MACAddress=${link.mac}
%endif
%if link.mtu:
MTUBytes=${link.mtu}
% endif
[Bridge]
VLANFilter=${link.vlan_filter}
