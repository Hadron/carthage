# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from pathlib import Path
import os, shutil
from carthage.dependency_injection import *
from carthage.machine import AbstractMachineModel
from carthage.setup_tasks import *
from .network import NetworkLink
from .utils import mako_lookup
from carthage import ConfigLayout, sh

local_type_map = dict(
    bond = dict(
        netdev = "bond-netdev.mako",
        member_network = "bond-network.mako"),
    bridge = dict(
        netdev = "bridge-netdev.mako",
        member_network = "bridge-network.mako",
),
    physical = dict(
        link = "physical-link.mako",
    ),
    )

def templates_for_link(l: NetworkLink):
    templates = dict(
        network = "default-network.mako",
        )
    templates.update(local_type_map.get(l.local_type or 'physical', {}))
    for ml in l.member_of_links:
        if ml.local_type:
            map_entry = local_type_map.get(ml.local_type, {})
            for i in ('network',):
                if 'member_'+i in map_entry:
                    templates[i] = map_entry['member_'+i]
    return templates

class NotNeeded(Exception):
        '''
        Indicates that a particular template is not needed and should be skipped.  Raised in the template itself.
        '''
        pass
        
@inject(config_layout = ConfigLayout)
class SystemdNetworkModelMixin(SetupTaskMixin, AsyncInjectable):

    def __init__(self, **kwargs):
        from .machine import AbstractMachineModel
        super().__init__(**kwargs)
        if isinstance(self, AbstractMachineModel) and hasattr(self, 'machine_mixins'):
            self.machine_mixins = list(self.machine_mixins)+[SystemdNetworkInstallMixin]
            
    @setup_task("Generate Network Configuration")
    async def generate_network_config(self):
        await self.resolve_networking()
        networking_dir = Path(self.stamp_path)/"networking"
        try: shutil.rmtree(networking_dir)
        except FileNotFoundError: pass
        os.makedirs(networking_dir)
        for link in self.network_links.values():
            self._render_network_configuration(link, networking_dir)


    def _render_network_configuration(self, link: NetworkLink, dir: Path):
        templates = templates_for_link(link)
        for ext, template_name in templates.items():
            if ext.startswith('member_'): continue
            template = mako_lookup.get_template(template_name)
            try:
                rendering = template.render(
                    link = link,
                    NotNeeded = NotNeeded
                )
                output_fn = dir.joinpath( f"10-carthage-{link.interface}.{ext}")
                with output_fn.open("wt") as f:
                    f.write(rendering)
            except NotNeeded: pass
        

class SystemdNetworkInstallMixin(SetupTaskMixin):

    
    @setup_task("Install Systemd Networking")
    async def install_systemd_networking(self):
        async with self.filesystem_access() as path:
            try: networking_dir = Path(self.model.stamp_path)/"networking"
            except AttributeError:
                networking_dir = Path(self.stamp_dir)/"networking"
            if not networking_dir.exists(): raise SkipSetupTask
            await sh.rsync(
                "-a", "--delete",
                "--include=10-carthage-*",
                str(networking_dir)+"/",
                Path(path)/"etc/systemd/network",
                _bg = True,
                _bg_exc = False)
            
                
__all__ = [
    'SystemdNetworkModelMixin',
    'SystemdNetworkInstallMixin',
        ]
