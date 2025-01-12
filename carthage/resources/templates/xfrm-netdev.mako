<%
if not link.interface_id:
    raise ValueError('xfrm links must have an interface ID')
%>
<%inherit file="netdev-base.mako"/>
<%block name="local_type">
[Xfrm]
InterfaceId=${link.interface_id}
Independent=true 
</%block>
