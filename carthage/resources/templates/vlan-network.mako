<%inherit file="network-base.mako" />
<%block name="network">
%for v in link.member_of_links:
<%if v.local_type != "vlan":
    raise ValueError("Cannot mix vlan and other links")
%>
VLAN=${v.interface}
%endfor
</%block>
