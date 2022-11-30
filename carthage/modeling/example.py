# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .base import *
from .decorators import *
from carthage.network import NetworkConfig
from carthage import InjectionKey


class RouterConfig(NetworkConfigModel):

    internet = injector_access("internet")
    site_network = injector_access("site-network")
    add("eth0", net=internet, mac=None)
    add("eth1", net=site_network, mac=None)


class Layout(ModelGroup):

    class net_config(NetworkConfigModel):
        site_network = injector_access("site-network")
        add("eth0", net=site_network, mac=None)

    @provides(InjectionKey("internet"))
    class Internet(NetworkModel):

        #        bridge_name = "brint"
        pass

    class Red(Enclave):
        domain = "evil.com"

        @provides("site-network")
        class RedNet(NetworkModel):
            pass

        class router(MachineModel):
            add_provider(InjectionKey(NetworkConfig), RouterConfig)

        class samba(MachineModel):
            ansible_groups = ['samba']

        for u in ('george', 'sue', 'pat'):
            @dynamic_name(f'{u}_desktop')
            class desktop(MachineModel):
                name = f'{u}-desktop'
        del u
