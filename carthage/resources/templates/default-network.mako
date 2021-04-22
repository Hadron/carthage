<%
from carthage.systemd import NotNeeded
nontrivial = False
v4_config = link.merged_v4_config
%>
[Match]
Name=${link.interface}
[Network]
%if v4_config.dhcp:
DHCP=ipv4
<%nontrivial = True%>
%endif
%if v4_config.address:
Address=${str(v4_config.address)}
<%nontrivial = True %>
%endif
<%if not nontrivial:
    raise NotNeeded
%>
