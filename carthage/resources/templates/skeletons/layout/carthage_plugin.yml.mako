<%inherit file="../prototype.mako" />\
name: ${args.name}
%if args.package:
package: ${args.package}
python: .
%endif
${subrender('layout.py')}\
${subrender("carthage_plugin.py")}\
<%def name='help()'>
A general purpose carthage layout.
</%def>\
<%def name='arguments()'>
<%
parser.add_argument('--package', '-p',
                    help='Which package should the layout live in.  If not specified, the layout lives in an anonymous package.',
                    metavar='package')
%>
</%def>\
