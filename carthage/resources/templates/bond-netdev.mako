[NetDev]
Name=${link.interface}
Kind=bond
%if link.mtu:
MTUBytes=${link.mtu}
% endif
[Bond]
Mode = 802.3ad
TransmitHashPolicy=layer3+4
