<%inherit file="netdev-base.mako"/>
<%block name="local_type">
[Tunnel]
Local=${str(link.local)}
Remote=${str(link.remote)}
% if link.key:
Key=${link.key}
% endif
</%block>
