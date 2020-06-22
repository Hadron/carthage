import contextlib
import os, os.path
from .dependency_injection import *
from . import sh
from .config import ConfigLayout
from .utils import memoproperty
from . import machine
from .setup_tasks import *
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
        self.ca_cert
        sh.entanglement_pki(host, d=self.pki_dir)

    @memoproperty
    def pki_dir(self):
        return os.path.join(self.config_layout.state_dir, "pki")

    @property
    def ca_cert(self):
        sh.entanglement_pki('-d', self.pki_dir,
                            '--ca-name', "Carthage Root CA")
        with open(self.pki_dir+'/ca.pem','rt') as f:
            return f.read()
        
@inject_autokwargs(
    pki = PkiManager)
class PkiCustomizations(machine.BaseCustomization):

    @setup_task("Install Carthage Root Cert")
    async def install_carthage_root_cert(self):
        from .container import Container
        host = self.host
        async with contextlib.AsyncExitStack() as stack:
            if isinstance(host,Container) and not host.running:
                path = host.volume.path
                run_on_host = host.container_command
            else:
                path = await stack.enter_async_context(host.filesystem_access())
                run_on_host = host.ssh
            carthage_cert_dir = os.path.join(
                path,
                "usr/share/ca-certificates/carthage")
            os.makedirs(carthage_cert_dir, exist_ok = True)
            with open(os.path.join(
                    carthage_cert_dir, "carthage.crt"),
                      "wt") as f:
                f.write(self.pki.ca_cert)
            with open(os.path.join(
                    path, "etc/ca-certificates.conf"),
                      "ta") as f:
                f.write("carthage/carthage.crt\n")
            await run_on_host("/usr/sbin/update-ca-certificates")


__all__ = ["PkiManager", "PkiCustomizations"]
