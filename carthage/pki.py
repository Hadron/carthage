# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import os, os.path
from .dependency_injection import *
from . import sh
from .config import ConfigLayout

@inject(config_layout = ConfigLayout)
class PkiManager(Injectable):

    def __init__(self, config_layout):
        self.config_layout = config_layout
        os.makedirs(self.pki_dir, exist_ok = True)

    def credentials(self, host):
        "Returns a key combined with certificate"
        self._certify(host)
        s = ""
        for ext in ('pem', 'key'):
            with open(os.path.join(self.pki_dir, "{}.{}".format(host, ext))) as f:
                s += f.read()
        return s

    def _certify(self, host):
        sh.entanglement_pki(host, d=self.pki_dir)

    @property
    def pki_dir(self):
        return os.path.join(self.config_layout.state_dir, "pki")

    @property
    def ca_cert(self):
        "Only valid after credentials called"
        with open(self.pki_dir+'/ca.pem','rt') as f:
            return f.read()
        
