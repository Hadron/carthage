[NetDev]
Name=${link.interface}
Kind=bond
%if link.mtu:
MTUBytes=${link.mtu}
MACAddress=${link.mac if link.mac != "inherit" else "none"}
% endif
[Bond]
Mode = 802.3ad
TransmitHashPolicy=layer3+4
