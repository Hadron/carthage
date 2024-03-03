<%inherit file="network-base.mako" />

<%block name="network" args="link">
<%
from carthage.systemd import NotNeeded
v4_config = link.merged_v4_config
if v4_config.pool:
    link.net.assign_addresses(link)
%>\
<% nontrivial = False %>\
%if v4_config.dhcp:
DHCP=ipv4
<%nontrivial = True%>\
%endif
%if v4_config.address and not v4_config.dhcp:
Address=${str(v4_config.address)}/${v4_config.network.prefixlen}
<%nontrivial = True %>
%endif
%if v4_config.secondary_addresses:
<% nontrivial = True %>
%for address in v4_config.secondary_addresses:
Address=${str(address.private)}/${v4_config.network.prefixlen}
%endfor
<%nontrivial = True %>
%endif
%if link.precious:
KeepConfiguration = dhcp
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
%if v4_config.gateway and not v4_config.dhcp:
Gateway=${v4_config.gateway}
%endif
<%if not nontrivial:
    raise NotNeeded
%>
[DHCPv4]
%if v4_config.dhcp and (v4_config.gateway is False):
UseGateway=no
%endif
</%block>
