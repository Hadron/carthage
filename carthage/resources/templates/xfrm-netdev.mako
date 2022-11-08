<%
if not link.interface_id:
    raise ValueError('xfrm links must have an interface ID')
%>
[NetDev]
Name=${link.interface}
Kind=xfrm
%if link.mtu:
MTUBytes=${link.mtu}
% endif
[Xfrm]
InterfaceId=${link.interface_id}
Independent=true 
