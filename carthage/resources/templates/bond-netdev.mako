[NetDev]
name=${link.interface}
Kind=bond
%if link.mtu:
MTUBytes=${link.mtu}
% endif
