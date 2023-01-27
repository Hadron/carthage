#!/usr/bin/python3
# Copyright (C) 2022, 2023, Hadron Industries, Inc.
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
from carthage.podman import *
from carthage.debian import *
import carthage
import carthage_base
_dir = Path(__file__).parent

class layout(CarthageLayout):
    add_provider(config_key('debian.debootstrap_options'), "--variant=minbase --include=systemd")
    add_provider(ConfigLayout)
    add_provider(carthage.ansible.ansible_log, str(_dir/"ansible.log"))

    @provides(container_image)
    class OurBaseImage(DebianContainerImage):
        name = 'base-carthage'
        install = wrap_container_customization(install_stage1_packages_task(['ansible']))

    oci_interactive = True

    @provides('from_scratch_debian')
    class FromScratchDebian(PodmanFromScratchImage):
        oci_image_cmd = 'bash'
        oci_image_tag = 'localhost/from_scratch_debian'

    class CarthageImage(PodmanImageModel):
        add_provider(oci_container_image, injector_access('from_scratch_debian'))
        oci_image_tag = 'localhost/carthage:latest'
        oci_image_cmd = '/bin/systemd'

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

    class Build(CarthageRunnerCommand):
        name = 'build'

        def setup_subparser(self, parser): pass

        async def run(self, args):
            layout = await self.ainjector(CarthageLayout)
            await layout.CarthageImage.build_image()
            
