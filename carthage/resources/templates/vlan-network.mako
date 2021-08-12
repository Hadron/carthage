<%inherit file="network-base.mako" />
<%namespace name = "default" file="default-network.mako" />
<%block name="network">
<%
from carthage.systemd import NotNeeded
from carthage.network.switch import link_vlan_config
vlan_link = link_vlan_config(link)
native_link = None
%>\
%for v in link.member_of_links:
<%
if v.local_type == 'none': continue
if v.local_type != "vlan":
    raise ValueError("Cannot mix vlan and other links")
%>\
%if vlan_link and (getattr(v, 'vlan_id', 'notequal') ==  vlan_link.untagged_vlan):
<%native_link = v%>\
%else:
VLAN=${v.interface}
%endif
%endfor
<%
try:
    if vlan_link and vlan_link.untagged_vlan: default.network(link = link)
    if native_link: logger.warning(f'Rendering {link}: Native link {native_link.interface} not used because rendered link had networking config')
    
except NotNeeded:
    if native_link:
        if native_link.member_of: raise TypeError(f'{native_link} is an invalid native link because it is a member of another link.  Directly connect those links to the VLAN trunk')
        try: default.network(link = native_link)
        except NotNeeded: logger.error(f'{native_link.interface} not used for {link.machine.name} because it is a native VLAN link and there is no network configuration present')
%>
</%block>
