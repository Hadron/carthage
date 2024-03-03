[NetDev]
Name=${link.interface}
Kind=gre
%if link.mtu:
MTUBytes=${link.mtu}
% endif

[Tunnel]
Local=${str(link.local)}
Remote=${str(link.remote)}
% if link.key:
Key=${link.key}
% endif
