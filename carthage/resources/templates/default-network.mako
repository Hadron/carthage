<%
from carthage.systemd import NotNeeded
nontrivial = False
v4_config = link.merged_v4_config
driver = "ether"
if link.local_type == "bridge":
    driver = "bridge"

%>
[Match]
%if link.mac:
Type=${driver}
MACAddress=${link.mac}
%else:
Name=${link.interface}
%endif
[Network]
%if v4_config.dhcp:
DHCP=ipv4
<%nontrivial = True%>
%endif
%if v4_config.address:
Address=${str(v4_config.address)}/${v4_config.network.prefixlen}
<%nontrivial = True %>
%endif
%if v4_config.domains:
Domains=${v4_config.domains}
%endif
%if v4_config.dns_servers:
DNS=${" ".join(v4_config.dns_servers)}
%endif
%if v4_config.masquerade:
IPMasquerade=yes
%endif
%if v4_config.gateway:
Gateway=${v4_config.gateway}
%endif
<%if not nontrivial:
    raise NotNeeded
%>
