[Match]
<%
if not link.mac: raise NotNeeded
%>\
MACAddress=${link.mac}
[Link]
% if link.member_of:
## Networkd breaks if you rename an interface that will be enslaved
AlternativeName=${link.interface}
%else:
Name=${link.interface}
%endif
NamePolicy=
