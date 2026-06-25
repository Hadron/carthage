<%inherit file="network-base.mako" />
<%namespace name="default" file="default-network.mako" />
<%block name="network">
<%
from carthage.systemd import NotNeeded
v4_config = link.merged_v4_config
%>\\
%if v4_config.dhcp:
DHCP=ipv4
%endif
%if v4_config.address and not v4_config.dhcp:
Address=${str(v4_config.address)}/${v4_config.network.prefixlen}
%endif
%if v4_config.secondary_addresses:
%for address in v4_config.secondary_addresses:
Address=${str(address.private)}/${v4_config.network.prefixlen}
%endfor
%endif
%if link.precious:
KeepConfiguration = dhcp
%endif
%if v4_config.domains:
Domains=${v4_config.domains}
%endif
%if v4_config.dns_servers:
%for s in v4_config.dns_servers:
DNS=${s}
%endfor
%endif
%if v4_config.gateway and not v4_config.dhcp:
[Route]
Gateway=${v4_config.gateway}
%if v4_config.metric:
Metric=${v4_config.metric}
%endif
%endif
</%block>