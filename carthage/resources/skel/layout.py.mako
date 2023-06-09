import carthage
from carthage import *
from carthage.ansible import *
from carthage.container import *
from carthage.debian import *
from carthage.dependency_injection import *
from carthage.image import *
from carthage.machine import *
from carthage.modeling import *
from carthage.network import V4Config, collect_vlans
from carthage.ssh import SshKey
from carthage.systemd import *
from carthage.vm import Vm, vm_image

import sh


class Layout(CarthageLayout, AnsibleModelMixin):

    layout_name = "${name}"

    add_provider(machine_implementation_key, dependency_quote(LocalMachine))

    class local_machine(MachineModel):

        async def async_ready(self):
            self.name = await sh.hostname("-f")
            return await super().async_ready()
