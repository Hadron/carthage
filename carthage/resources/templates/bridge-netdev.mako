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
VLANFiltering=${link.vlan_filter}
