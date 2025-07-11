#!/usr/bin/python3
# Copyright (C) 2022, 2023, 2025, Hadron Industries, Inc.
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
from carthage.debian import DebianContainerCustomizations
from carthage.container import container_image
import carthage
import carthage.console
import carthage_base
_dir = Path(__file__).parent.parent

class layout(CarthageLayout):
    add_provider(carthage.ansible.ansible_log, str(_dir/"ansible.log"))
    add_provider(config_key('debian.distribution'), 'trixie')
    add_provider(ConfigLayout)

    add_provider(OciMount(destination='/var/lib/apt/lists'))
    
    @inject(base_image=None)
    class VolumeAccess(PodmanImageModel):
        '''
        The Debian distribution we are using with an sftp server installed. Used by the podman plugin to gain sftp access to volumes.
        '''
    
        oci_image_tag = 'ghcr.io/hadron/carthage_volume_access:latest'
        base_image = 'debian:trixie'
        add_provider(podman_push_images, True)

        class InstallSftpServer(ContainerCustomization):
            install_sftp = install_stage1_packages_task(['openssh-sftp-server'], install_recommends=False)
        
    @inject(base_image=None)
    class OurBaseImage(PodmanImageModel):
        name = 'base-carthage'
        base_image =injector_access('VolumeAccess')
        oci_image_tag = 'localhost/carthage_debian_base:latest'

        class install(ContainerCustomization):
            install_software = install_stage1_packages_task(['ansible', 'systemd'])

        debian_customizations = DebianContainerCustomizations

    oci_interactive = True

    class CarthageImage(PodmanImageModel, carthage_base.CarthageServerRole):
        base_image = injector_access('OurBaseImage')
        oci_image_tag = 'ghcr.io/hadron/carthage:latest'
        oci_image_command = ['/sbin/init']

        add_provider(podman_push_images, True)
        add_provider(OciEnviron('PYTHONPATH=/carthage'))
        add_provider(OciEnviron('PATH=/carthage/bin:/usr/bin:/usr/sbin:/usr/local/bin'))

        class customize_for_oci(FilesystemCustomization):


            @setup_task("Install service")
            async def install_service(self):
                shutil.copyfile(_dir/"console.service", self.path/"etc/systemd/system/console.service")
                shutil.copy2(_dir/"start-carthage.sh", self.path/"start-carthage.sh")
                await self.run_command("/bin/systemctl", "mask", "console-getty", )
                await  self.run_command("/bin/systemctl", "enable", "console")

