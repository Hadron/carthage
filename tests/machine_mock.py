# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import os.path
from tempfile import TemporaryDirectory
from carthage import sh


class Machine:

    ssh_options = []

    def __init__(self, name):
        self.name = name
        self.dir = TemporaryDirectory()
        self.dir.__enter__()

    @property
    def ssh(self):
        environ = os.environ.copy()
        if 'PYTHONPATH' in environ:
            ppath_expanded = environ['PYTHONPATH'].split(':')
            ppath = ':'.join(map(lambda x: os.path.abspath(x), ppath_expanded))
            environ['PYTHONPATH'] = ppath

        class CommandFinder:

            def __call__(self, cmd, *args, **kwargs):
                cmd = getattr(self, cmd)
                return cmd(*args, **kwargs)

            def __getattr__(inner_self, c):
                c = getattr(sh, c)
                return c.bake(_cwd=self.dir.name,
                              _env=environ)
        return CommandFinder()

    def rsync_path(self, p):
        return os.path.join(self.dir.name,
                            os.path.relpath(p, start="/"))

    def close(self):
        self.dir.__exit__(None, None, None)

    def __del__(self):
        self.close()

    @property
    def path(self): return self.dir.name
