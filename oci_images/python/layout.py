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

from carthage import *
from carthage.modeling import *
from carthage.podman import *
from carthage.oci import *
from carthage.debian import *
from carthage.container import container_image
import carthage
import carthage.console
import carthage_base
_dir = Path(__file__).parent.parent

class layout(CarthageLayout):
    add_provider(config_key('debian.debootstrap_options'), "--variant=minbase --include=systemd")
    add_provider(ConfigLayout)
    add_provider(carthage.ansible.ansible_log, str(_dir/"ansible.log"))

    @provides(podman_image_volume_key)
    class OurBaseImage(DebianContainerImage):
        name = 'base-carthage'
        install = wrap_container_customization(install_stage1_packages_task(['ansible']))

    oci_interactive = True
    # We could simply use the 'debian:bookworm' docker image.  That
    # may even be a better choice.  However in an attempt to change
    # only one thing at a time migrating from the old image building,
    # I'm doing this.
    @provides('from_scratch_debian')
    class FromScratchDebian(PodmanFromScratchImage):
        oci_image_command = ['bash']
        oci_image_tag = 'localhost/from_scratch_debian'

    class CarthageImage(PodmanImageModel, carthage_base.CarthageServerRole):
        base_image = injector_access('from_scratch_debian')
        oci_image_tag = 'localhost/carthage:latest'
        oci_image_command = ['/bin/systemd']

        add_provider(OciEnviron('PYTHONPATH=/carthage'))

        class customize_for_oci(FilesystemCustomization):

            @setup_task("Remove Software")
            async def remove_software(self):
                await self.run_command("apt", "-y", "purge",
                                       "exim4-base",
                                       )

            @setup_task("Install service")
            async def install_service(self):
                shutil.copyfile(_dir/"console.service", self.path/"etc/systemd/system/console.service")
                shutil.copy2(_dir/"start-carthage.sh", self.path/"start-carthage.sh")
                await self.run_command("/bin/systemctl", "mask", "console-getty", )
                await  self.run_command("/bin/systemctl", "enable", "console")

    class Build(carthage.console.CarthageRunnerCommand):
        name = 'build'

        def setup_subparser(self, parser): pass

        async def run(self, args):
            layout = await self.ainjector.get_instance_async(CarthageLayout)
            await layout.CarthageImage.build_image()
            
