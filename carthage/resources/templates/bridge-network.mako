<%!
from carthage.network.switch import link_vlan_config
%>\
<%inherit file="network-base.mako" />
<%block name="network">
Bridge=${link.member_of[0]}
</%block>
<%block name="trailer">
<%vlan_link = link_vlan_config(link) %>\
%if vlan_link and link.member_of_links[0].vlan_filter:
%for vlan in vlan_link.allowed_vlans:
<%untagged_included = False %>\
[BridgeVLAN]
%if isinstance(vlan, slice):
VLAN=${vlan.start}-${vlan.end}
<%
if vlan_link.untagged_vlan in range(vlan.start, vlan.end+1): untagged_included = True
%>\
%elif isinstance(vlan,int):
<%if vlan == vlan_link.untagged_vlan: untagged_included = True %>\
VLAN=${vlan}
%endif
%if untagged_included:
PVID=${vlan_link.untagged_vlan}
%endif
%endfor
%endif
</%block>
