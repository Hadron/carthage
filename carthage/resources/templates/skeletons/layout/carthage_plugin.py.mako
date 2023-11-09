<%inherit file="../prototype.mako" />\
<%!
from carthage.skeleton import package_to_dir
%>\
<%def name='output()' >
%if args.package:
${package_to_dir(args.package)}/__init__.py
%else:
carthage_plugin.py
%endif
    </%def>\
from carthage import inject, Injector
from . import layout

@inject(injector=Injector)
def carthage_plugin(injector):
    injector.add_provider(layout.layout)
