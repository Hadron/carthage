# Copyright (C) 2021, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import json
import logging
import os
import re
from pathlib import Path

from .setup_tasks import *
from .dependency_injection import *
from .config import ConfigLayout
from .network import hash_network_links
logger = logging.getLogger('carthage.network')
__all__ = []

port_number_re = re.compile(r'(Ethernet|PortChannel)([0-9]+)')
speed_re = re.compile(r'([0-9]+)[gG]')

def sonic_port_config(model, breakout_config, network_links):
    def config_for(link, breakout_mode):
        nonlocal current_breakout
        nonlocal port_config
        breakout_info = breakout_config[link]
        indexes = breakout_info['index'].split(',')
        lanes = breakout_info['lanes'].split(',')
        if breakout_mode not in breakout_info['breakout_modes']:
            logger.error(f'{model.name}: {breakout_mode} not valid breakout mode for {link}')
            return
        aliases = breakout_info['breakout_modes'][breakout_mode]
        current_breakout[link] = dict(brkout_mode=breakout_mode)
        num_ports = len(aliases)
        num_lanes = int(len(lanes)/num_ports)
        speed = int(speed_re.search(breakout_mode).group(1))*1000
        offset = 0
        portbase= int(port_number_re.match(link).group(2))
        for portnum in range(portbase, portbase+num_ports, 1):
            port_config[f'Ethernet{portnum}'] = dict(
                admin_status='up',
                lanes=",".join(lanes[(offset*num_lanes):((offset+1)*num_lanes)]),
                index=str(indexes[offset]),
                alias=aliases[offset],
                mtu="9100",
                speed=str(speed),
                )
            offset += 1
            
    port_config = {}
    current_breakout = {}
    for link in breakout_config:
        breakout_mode = breakout_config[link]['default_brkout_mode']
        if link in network_links:
            try: breakout_mode = network_links[link].breakout_mode
            except AttributeError: pass
        config_for(link, breakout_mode)
    for link in network_links:
        if not link.startswith('Ethernet'): continue
        if network_links[link].local_type: continue
        if link not in port_config:
            logger.error(f'{model.name}: {link} configured but not present in physical breakout modes')
            continue
        nl = network_links[link]
        if nl.mtu: port_config[link]['mtu'] = str(nl.mtu)
        # Speed defaults to one specified in breakout mode.  Next
        # lowest priority is speed from other side of link, then speed
        # from this side of the link.
        try: port_config[link]['speed'] = str(nl.other.speed)
        except AttributeError: pass
        try: port_config[link]['speed'] = str(nl.speed)
        except AttributeError: pass

    return [
        dict(
            op='add',
            path='/PORT',
            value=port_config),
        dict(
            op='add',
            path='/BREAKOUT_CFG',
            value=current_breakout)]

@inject(config_layout=ConfigLayout)
class SonicNetworkModelMixin(SetupTaskMixin, AsyncInjectable):

    '''
A mixin for :class:`AbstractMachineModel` for SONiC network switches.  
'''

    @setup_task("Generate SONiC Config patch")
    async def sonic_config(self):
        await self.resolve_networking()
        breakout_json_path = self.stamp_path.joinpath("breakout.json")
        if not breakout_json_path.exists(): raise SkipSetupTask
        breakout_json = breakout_json_path.read_text()
        breakout_config = json.loads(breakout_json)
        with self.stamp_path.joinpath("carthage-sonic-config.json").open("wt") as f:
            f.write(json.dumps(sonic_port_config(self, breakout_config, self.network_links),
                               indent=4))

    @sonic_config.hash()
    def sonic_config(self):
        return str(hash_network_links(self.network_links))

    @sonic_config.invalidator()
    def sonic_config(self, last_run):
        breakout_path = self.stamp_path.joinpath("breakout.json")
        try: stat = breakout_path.stat()
        except FileNotFoundError: return False
        return stat.st_mtime > last_run

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        from carthage.modeling import MachineMixin
        self.injector.add_provider(
            InjectionKey(MachineMixin, name="SonicNetworkInstallMix in"),
            dependency_quote(SonicNetworkInstallMixin))
        
class SonicNetworkInstallMixin(SetupTaskMixin):

    @setup_task("Get breakout config")
    async def get_sonic_breakout_config(self):
        breakout = await self.ssh("show interface breakout",
                                  _bg=True,
                                  _bg_exc=True,
                                  )
        breakout_path = self.model.stamp_path/"breakout.json"
        with breakout_path.open("wt") as f:
            f.write(str(breakout.stdout,'UTF-8'))
        await self.model.ainjector(self.model.sonic_config)
        
__all__ += ['SonicNetworkModelMixin']
