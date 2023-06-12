<%block name="copyright">\
%if not args.proprietary:
<%
import datetime
year = datetime.datetime.now().year
%>\
# Copyright (C) ${year}, ${args.copyright}.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
%endif

</%block>\
${next.body()}\
