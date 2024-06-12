# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import contextlib
import logging
import os
import re
import tempfile
from .utils import import_resources_files
from pathlib import Path
from .dependency_injection import *
from .setup_tasks import *
from .config import ConfigLayout
from .image import ContainerImage, ContainerCustomization, ContainerVolume, ImageVolume
from .machine import customization_task
from . import sh

logger = logging.getLogger('carthage')

file_re = re.compile(r'file:/+(/[^/]+.*)')

__all__ = []


def bind_args_for_mirror(mirror):
    match = file_re.match(mirror)
    if match:
        return [f'--bind={match.group(1)}']
    return []


__all__ += ['bind_args_for_mirror']


@inject_autokwargs(config_layout=ConfigLayout)
class DebianContainerCustomizations(ContainerCustomization):

    description = "Set up Debian for Carthage"

    @setup_task("Turn on networkd")
    async def turn_on_networkd(self):
        root = Path(self.path)
        if not root.joinpath("usr/bin/resolvectl").exists():
            await self.container_command('apt', 'update')
            await self.container_command('apt', '-y', 'install', 'systemd-resolved')
        await self.container_command("systemctl", "enable", "systemd-networkd", "systemd-resolved")

    @setup_task("Install python and dbus")
    async def install_python(self):
        bind_args = bind_args_for_mirror(self.config_layout.debian.stage1_mirror)
        async with use_stage1_mirror(self):
            await self.container_command(*bind_args,
                                         "apt", "update")
            await self.container_command(*bind_args,
                                         "apt-get", "-y", "install", "python3", "dbus")


class DebianContainerImage(ContainerImage):

    mirror: str
    distribution: str
    include_security: bool
    name: str = "base-debian"

    def __init__(self, name: str = None,
                 mirror: str = None, distribution: str = None,
                 stage1_mirror: str = None,
                 include_security: bool = None,
                 **kwargs):
        if name is None:
            name = self.__class__.name
        super().__init__(name=name, **kwargs)
        # Make sure our config layout points to our injector so we can
        # change things with only local effect.
        self.config_layout = self.injector(ConfigLayout)
        self.mirror = self.config_layout.debian.mirror
        self.stage1_mirror = self.config_layout.debian.stage1_mirror
        self.distribution = self.config_layout.debian.distribution
        self.include_security = self.config_layout.debian.include_security
        self.debootstrap_options = self.config_layout.debian.debootstrap_options
        if mirror:
            self.mirror = mirror
            if not stage1_mirror:
                self.stage1_mirror = mirror
        if distribution:
            self.distribution = distribution
        if stage1_mirror:
            self.stage1_mirror = stage1_mirror
        if include_security is not None:
            self.include_security = include_security
        # Make sure we save the right items in case a customization ends up calling update_mirror.
        dc = self.config_layout.debian
        dc.mirror = self.mirror
        dc.distribution = self.distribution
        dc.stage1_mirror = self.stage1_mirror
        dc.include_security = self.include_security

    @setup_task("unpack using debootstrap")
    async def unpack_container_image(self):
        await sh.debootstrap('--include=openssh-server,ca-certificates',
                             *(self.debootstrap_options.split()),
                             self.distribution,
                             self.path, self.stage1_mirror,
                             _bg=True,
                             _bg_exc=False)
        path = Path(self.path)
        try:
            os.unlink(path / "etc/hostname")
        except FileNotFoundError:
            pass
        try:
            with path.joinpath("etc/ssh/sshd_config").open("at") as f:
                f.write("PasswordAuthentication no")
        except FileNotFoundError:
            pass


    @setup_task("Update mirror")
    def update_mirror(self):
        update_mirror(self.path, self.mirror, self.distribution, self.include_security)

    debian_customizations = customization_task(DebianContainerCustomizations)


__all__ += ['DebianContainerImage']


def update_mirror(path, mirror, distribution, include_security):
    etc_apt = Path(path) / "etc/apt"
    sources_list = etc_apt / "sources.list"
    if sources_list.exists():
        os.unlink(sources_list)
    debian_list = etc_apt / "sources.list.d/debian.list"
    os.makedirs(debian_list.parent, exist_ok=True)
    with debian_list.open("wt") as f:
        f.write(f'''
deb {mirror} {distribution} main contrib non-free
deb-src {mirror} {distribution} main contrib non-free
''')
        if include_security and distribution != 'unstable':
            f.write(f'''
deb {mirror} {distribution}-updates main contrib non-free
deb-src {mirror} {distribution}-updates main contrib non-free

deb https://security.debian.org/ {distribution}-security main
''')


@contextlib.asynccontextmanager
async def use_stage1_mirror(machine):
    debian = machine.config_layout.debian
    async with machine.filesystem_access() as path:
        try:
            update_mirror(path, debian.stage1_mirror, debian.distribution,
                          (debian.include_security and (debian.mirror == debian.stage1_mirror)))
            if machine.running:
                await machine.ssh("apt", "update",
                                  _bg=True, _bg_exc=False)
            else:
                await machine.container_command(*bind_args_for_mirror(debian.stage1_mirror),
                                                "apt", "update")
            yield
        finally:
            update_mirror(path, debian.mirror, debian.distribution, debian.include_security)
            try:
                if machine.running:
                    await machine.ssh("apt", "update",
                                      _bg=True, _bg_exc=False)
                else:
                    await machine.container_command(*bind_args_for_mirror(debian.mirror),
                                                    "apt", "update")
            except BaseException:
                logger.exception("Error cleaning up mirror")

__all__ += ['use_stage1_mirror']


def install_stage1_packages_task(packages):
    @setup_task(f'Install {packages} using stage 1 mirror')
    async def install_task(self):
        async with use_stage1_mirror(self):
            mirror = self.config_layout.debian.stage1_mirror
            await self.container_command(
                *bind_args_for_mirror(mirror),
                'apt', '-y',
                'install', *packages)
    return install_task


__all__ += ['install_stage1_packages_task']


@inject(ainjector=AsyncInjector, config=ConfigLayout)
async def debian_container_to_vm(
        volume: ContainerVolume,
        output: str,
        size: str,
        classes: str = None,
        *,
        config,
        image_volume_class=ImageVolume,
        ainjector):
    '''

    Use FAI to convert a container image into a VM image.  Carthage's FAI configuration currently requires that the host running Carthage (and probably the container being converted) use systemd-resolved for DNS.

    :param size: Size of resulting disk; for example ``8g``

    :param classes: The FAI classes to use.  If this starts with a "+", then add to the default classes.  The following classes are available:

        * SERIAL: enable serial console

        * DEFAULT: mandatory behavior

        * GROW: grow the root partition to fill the disk if the disk is expanded

        * GRUB_EFI: Install EFI version of grub.

        * CLOUD_INIT: Enable cloud-init


    '''
    from .container import logger

    def out_cb(data):
        data = data.strip()
        logger.debug("Image Creation: %s", data)

    async def unpack_callback(image_volume):
        nonlocal volume
        if isinstance(volume, type):
            # Allow usage specifying volume in a when_needed
            volume = await ainjector(volume)
        await volume.async_become_ready()
        os.makedirs(output_path.parent, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=output_path.parent,
                                         prefix="container-to-vm-") as tmp_d:
            tmp = Path(tmp_d).absolute()
            await sh.tar(
                "-C", str(volume.path),
                "--xattrs",
                "--xattrs-include=*.*",
                "-czf",
                str(tmp / "base.tar.gz"),
                ".",
                _bg=True,
                _bg_exc=False)
            env = os.environ.copy()
            env['FAI_BASE'] = str(tmp / "base.tar.gz")
            await sh.fai_diskimage(
                '-S', size,
                '-s', str(fai_configspace),
                '-c', classes,
                str(tmp / "image.raw"),
                _env=env,
                _bg=True,
                _bg_exc=False,
                _encoding='UTF-8',
                _out=out_cb)
            os.rename(tmp / "image.raw", output_path)

    default_classes = "DEFAULT,CONTAINER2VM,GRUB_EFI"
    if classes is None:
        classes = default_classes
    elif classes[0] == "+":
        classes = default_classes + ',' + classes[1:]

    output_path = Path(config.vm_image_dir).joinpath(output)
    return await ainjector(image_volume_class, name=output_path.absolute(),
                           unpack=unpack_callback,
                           )

__all__ += ['debian_container_to_vm']

#: The Carthage FAI configuration space
fai_configspace = import_resources_files(__package__) / "resources/fai"
