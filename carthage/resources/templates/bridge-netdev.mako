[NetDev]
Name=${link.interface}
Kind=bridge
%if link.mtu:
MTUBytes=${link.mtu}
% endif
