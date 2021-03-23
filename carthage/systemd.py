from pathlib import Path
import os, shutil
from carthage.dependency_injection import *
from carthage.machine import AbstractMachineModel
from carthage.setup_tasks import *
from .network import NetworkLink
from .utils import mako_lookup
from carthage import ConfigLayout

local_type_map = dict(
    bond = dict(
        netdev = "bond-netdev.mako",
        member_network = "bond-network.mako"),
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
class SystemdNetworkModelMixin(SetupTaskMixin):

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
        
        
        
__all__ = [
    'SystemdNetworkModelMixin',
    ]
