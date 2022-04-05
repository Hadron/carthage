#!/usr/bin/python3
# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from pathlib import Path
import sys
import tempfile
import shutil
sys.path.insert(0,'..')
from carthage import *
from carthage.modeling import *
from carthage.container import *
from carthage.debian import *
import carthage
_dir = Path(__file__).parent

def layout():
    import carthage_base
    class layout(CarthageLayout):
        add_provider(machine_implementation_key, dependency_quote(Container))
        add_provider(config_key('debian.debootstrap_options'), "--variant=minbase --include=systemd")
        add_provider(ConfigLayout)

        @provides(container_image)
        class OurBaseImage(DebianContainerImage):
            name = 'base-carthage'
            install = wrap_container_customization(install_stage1_packages_task(['ansible']))
            
        class carthage_server(carthage_base.CarthageServerRole, MachineModel):
            name = 'carthage-image'

            class customize_for_oci(FilesystemCustomization):

                @setup_task("Remove Software")
                async def remove_software(self):
                    await self.run_command("apt", "-y", "purge",
                                           "exim4-base",
                                           _bg=True, _bg_exc=False)
                    
                @setup_task("Install service")
                async def install_service(self):
                    shutil.copyfile(_dir/"console.service", self.path/"etc/systemd/system/console.service")
                    shutil.copy2(_dir/"start-carthage.sh", self.path/"start-carthage.sh")
                    await self.run_command("/bin/systemctl", "mask", "console-getty", _bg=True, _bg_exc=False)
                    await  self.run_command("/bin/systemctl", "enable", "console", _bg=True, _bg_exc=False)

    return layout

@inject(ainjector=AsyncInjector)
async def build_oci_image(container: Container, container_file:str, image_tag:str, ainjector):
    config = await ainjector(ConfigLayout)
    with tempfile.TemporaryDirectory(dir=config.vm_image_dir) as d_str:
        d = Path(d_str)
        shutil.copyfile(container_file, d/'Containerfile')
        await sh.tar(
            "-C"+str(container.volume.path),
            '-cp',
            "-f"+str(d/"container.tar"),
            "--xattrs",
            "--xattrs-include=*.*",
            ".",
            _bg=True,
            _bg_exc=False)
        await sh.podman(
            "build",
            "-t"+image_tag,
            str(d),
            _bg=True,
            _bg_exc=False)
        
@inject(ainjector=AsyncInjector)
async def run(ainjector):
    await ainjector(carthage.plugins.load_plugin, 'https://github.com/hadron/carthage_base')
    ainjector.add_provider(layout())
    l = await ainjector.get_instance_async(CarthageLayout)
    await l.carthage_server.machine.async_become_ready()
    await ainjector(build_oci_image, l.carthage_server.machine, str(_dir/"Containerfile"), "carthage:latest")
    

if __name__ == '__main__':
    parser = carthage.utils.carthage_main_argparser()
    args = carthage.utils.carthage_main_setup(parser)
    carthage.utils.carthage_main_run(run)
    
