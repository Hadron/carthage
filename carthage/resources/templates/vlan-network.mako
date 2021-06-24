<%inherit file="network-base.mako" />
<%namespace name = "default" file="default-network.mako" />
<%block name="network">
%for v in link.member_of_links:
<%if v.local_type != "vlan":
    raise ValueError("Cannot mix vlan and other links")
%>
VLAN=${v.interface}
%endfor
<%
from carthage.systemd import NotNeeded
from carthage.network.switch import link_vlan_config
vlan_link = link_vlan_config(link)
try:
    if vlan_link and vlan_link.untagged_vlan: default.network()
except NotNeeded: pass
%>
</%block>
