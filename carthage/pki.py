# Copyright (C) 2018, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import contextlib
import pathlib
import os
import os.path
from .dependency_injection import *
from . import sh
from .config import ConfigLayout
from .utils import memoproperty
from . import machine
from .setup_tasks import *

@inject_autokwargs(config_layout=ConfigLayout)
class PkiManager(Injectable):

    def credentials(self, host):
        "Returns a key combined with certificate"
        self._certify(host)
        s = ""
        for ext in ('pem', 'key'):
            with open(os.path.join(self.pki_dir, "{}.{}".format(host, ext))) as f:
                s += f.read()
        return s

    def _certify(self, host):
        self.ca_cert
        sh.entanglement_pki(host, d=self.pki_dir, _bg=False)

    @memoproperty
    def pki_dir(self):
        ret = pathlib.Path(self.config_layout.state_dir)/"pki"
        ret.mkdir(exist_ok=True)
        return str(ret)

    @property
    def ca_cert(self):
        sh.entanglement_pki('-d', self.pki_dir,
                            '--ca-name', "Carthage Root CA", _bg=False)
        with open(self.pki_dir + '/ca.pem', 'rt') as f:
            return f.read()


__all__ = ['PkiManager']

def install_root_cert_customization(get_certificate_info):
    '''
    Return a customization that installs one or more root certificates.

    :param get_get_certificate_info: A callback function resolved in the context of the customization's :class:`AsyncInjector` that returns a sequence of [certificate_name, pem_certificate].  The *certificate_name* uniquely identifies the certificate and *pem_certificate* is the root certificate to add.

    '''
    class InstallRootCertCustomization(machine.FilesystemCustomization):

        @setup_task("Install  Root Certs")
        async def install_root_cert(self):
            certificates_to_install = await self.ainjector(get_certificate_info)
            carthage_cert_dir = os.path.join(
                    self.path,
                    "usr/share/ca-certificates/carthage")
            os.makedirs(carthage_cert_dir, exist_ok=True)
            for name, pem_cert in certificates_to_install:
                with open(os.path.join(
                        carthage_cert_dir, f"{name}.crt"),
                          "wt") as f:
                    f.write(pem_cert)
                with open(os.path.join(
                        self.path, "etc/ca-certificates.conf"),
                          "ta") as f:
                    f.write(f"carthage/{name}.crt\n")
            await self.run_command("/usr/sbin/update-ca-certificates")

    return InstallRootCertCustomization

__all__ += ['install_root_cert_customization']

@inject(
    pki=PkiManager)
def pki_manager_certificate_info(pki):
    return [('carthage_pki', pki.ca_cert)]

PkiCustomizations = install_root_cert_customization(pki_manager_certificate_info)

__all__ += ["PkiCustomizations"]
