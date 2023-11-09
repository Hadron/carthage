<%inherit file="../prototype.mako" />
<%!
from carthage.skeleton import package_to_dir
%>\
<%def name='output()'>
%if args.package:
${package_to_dir(args.package)}/layout.py
%else:
python/layout.py
%endif
</%def>\
from carthage import *
from carthage.modeling import *
from carthage.ansible import *
from carthage.network import V4Config
