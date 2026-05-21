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

    class CarthageLibvirtImage(PodmanImageModel):
        base_image = injector_access('CarthageImage')
        oci_image_tag = 'ghcr.io/hadron/carthage-libvirt:latest'
        oci_image_command = ['/sbin/init']
        add_provider(podman_push_images, True)

        class customize_for_oci_container(DebianContainerCustomizations):

            @setup_task("Configure qemu user and group")
            async def configure_qemu_user(self):
                await self.run_command("/usr/bin/sed", "-i", """$a'user = "root"\ngroup = "root"\nremember_owner = 0\n'""", self.path/"etc/libvirt/qemu.conf")

            @setup_task("Configure qemu to allow access to default network")
            async def configure_qemu_default_network(self):
                qemu_path = self.path/"etc/qemu"
                qemu_path.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(_dir/"bridge.conf", qemu_path/"bridge.conf")


            @setup_task("Install virt-manager")
            async def install_virt_manager(self):
                bind_args = bind_args_for_mirror(self.config_layout.debian.stage1_mirror)
                async with use_stage1_mirror(self):
                    await self.container_command(*bind_args,
                                                "apt", "update")
                    await self.container_command(*bind_args,
                                                "apt-get", "-y", "install", "virt-manager")

                # Enable default network to avoid conflict with pasta
                await self.container_command("/usr/bin/virsh", "net-autostart", "default")
                await self.container_command("/usr/bin/virsh", "net-start", "default")


        class customize_for_oci_fs(FilesystemCustomization):

            @setup_task("Compile setgroups LD_PRELOAD library")
            async def compile_segroups_ld_preload(self):
                shutil.copyfile(_dir/"disable_setgroups.c", self.path/"usr/local/src")
                await self.run_command("/usr/bin/gcc", "-c", "-o", self.path/"usr/local/lib", self.path/"usr/local/src/disable_setgroups.c")

            @setup_task("Override libvirtd systemd service with setgroups disabled")
            async def install_config(self):
                libvirtd_override_path = self.path/"etc/systemd/system/libvirtd.service.d"
                libvirtd_override_path.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(_dir/"libvirtd.conf", libvirtd_override_path/"override.conf")
