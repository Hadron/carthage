[Match]
<%
if not link.mac: raise NotNeeded
%>\
MACAddress=${link.mac}
[Link]
name=${link.interface}
NamePolicy=
