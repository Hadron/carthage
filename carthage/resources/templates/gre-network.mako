<%inherit file="default-network.mako" />

<%block name="trailer">
[Route]
Destination=${str(link.merged_v4_config.network)}

% for route in link.routes:
[Route]
Destination=${str(route.v4_config.network)}
Gateway=${str(route.v4_config.gateway)}
GatewayOnLink=no

% endfor
</%block>
