from __future__ import annotations
import contextlib, logging , os, re, tempfile
import importlib.resources
from pathlib import Path
from .dependency_injection import *
from .setup_tasks import *
from .config import ConfigLayout
from .image import ContainerImage, ContainerCustomization, BtrfsVolume, ImageVolume
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

@inject_autokwargs(config_layout = ConfigLayout)
class DebianContainerCustomizations(ContainerCustomization):

    description = "Set up Debian for Carthage"
    
    @setup_task("Turn on networkd")
    async def turn_on_networkd(self):
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

    def __init__(self, name:str = "base-debian",
                 mirror: str = None, distribution: str = None,
                 stage1_mirror: str = None,
                 **kwargs):
        super().__init__(name = name, **kwargs)
        self.mirror = self.config_layout.debian.mirror
        self.stage1_mirror = self.config_layout.debian.stage1_mirror
        self.distribution = self.config_layout.debian.distribution
        if mirror:
            self.mirror = mirror
            if not stage1_mirror: self.stage1_mirror = mirror
        if distribution: self.distribution = distribution
        if stage1_mirror: self.stage1_mirror = stage1_mirror

    @setup_task("unpack using debootstrap")
    async def unpack_container_image(self):
        await sh.debootstrap('--include=openssh-server',
                             self.distribution,
                             self.path, self.stage1_mirror,
                             _bg = True,
                             _bg_exc = False)
        path = Path(self.path)
        try: os.unlink(path/"etc/hostname")
        except FileNotFoundError: pass

    debian_customizations = customization_task(DebianContainerCustomizations)

    @setup_task("Update mirror")
    def update_mirror(self):
        update_mirror(self.path, self.mirror, self.distribution)

__all__ += ['DebianContainerImage']

def update_mirror(path, mirror, distribution):
    etc_apt = Path(path)/"etc/apt"
    sources_list = etc_apt/"sources.list"
    if sources_list.exists():
        os.unlink(sources_list)
    debian_list = etc_apt/"sources.list.d/debian.list"
    os.makedirs(debian_list.parent, exist_ok = True)
    with debian_list.open("wt") as f:
        f.write(f'''
deb {mirror} {distribution} main contrib non-free
deb-src {mirror} {distribution} main contrib non-free
''')

@contextlib.asynccontextmanager
async def use_stage1_mirror(machine):
    debian = machine.config_layout.debian
    async with machine.filesystem_access() as path:
        try:
            update_mirror(path, debian.stage1_mirror, debian.distribution)
            if machine.running:
                await machine.ssh("apt", "update",
                                  _bg = True, _bg_exc = False)
            else:
                await machine.container_command(*bind_args_for_mirror(debian.stage1_mirror),
                                                "apt", "update")
            yield
        finally:
            update_mirror(path, debian.mirror, debian.distribution)
            try:
                if machine.running:
                    await machine.ssh("apt", "update",
                                      _bg = True, _bg_exc = False)
                else:
                    await machine.container_command(*bind_args_for_mirror(debian.mirror),
                                                    "apt", "update")
            except: logger.exception("Error cleaning up mirror")
            
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

@inject(ainjector = AsyncInjector)
async def debian_container_to_vm(
        volume: BtrfsVolume,
        output: str,
        size: str,
        classes: str = None,
        *,
        image_volume_class = ImageVolume,
        ainjector):
    '''

    Use FAI to convert a container image into a VM image.

    :param size: Size of resulting disk; for example ``8g``

    :param classes: The FAI classes to use.  If this starts with a "+", then add to the default classes.  The following classes are available:

        * SERIAL: enable serial console

        * DEFAULT: mandatory behavior

        * GROW: grow the root partition to fill the disk if the disk is expanded

        * GRUB_EFI: Install EFI version of grub.


    '''
    from .container import logger
    def out_cb(data):
        data = data.strip()
        logger.debug("Image Creation: %s", data)
        
    default_classes = "DEFAULT,GRUB_EFI"
    fai_configspace = importlib.resources.files(__package__)/"resources/fai-container-to-vm"
    if classes is None: classes = default_classes
    elif classes[0] == "+":
        classes = default_classes+','+classes[1:]
    output_path = Path(output)
    os.makedirs(output_path.parent, exist_ok = True)
    with tempfile.TemporaryDirectory(dir = output_path.parent,
                                         prefix = "container-to-vm-") as tmp_d:
        tmp = Path(tmp_d).absolute()
        await sh.tar(
            "-C", volume.path,
            "--xattrs",
            "--xattrs-include=*.*",
            "-czf",
            str(tmp/"base.tar.gz"),
            ".",
            _bg = True,
            _bg_exc = False)
        env = os.environ.copy()
        env['FAI_BASE'] = str(tmp/"base.tar.gz")
        await sh.fai_diskimage(
            '-S', size,
            '-s', str(fai_configspace),
            '-c', classes,
            str(tmp/"image.raw"),
            _env = env,
            _bg = True,
            _bg_exc = False,
            _encoding = 'UTF-8',
            _out = out_cb)
        os.rename(tmp/"image.raw", output_path)
        return await ainjector(image_volume_class, name = output_path.absolute(),
                               unpack = False,
                               remove_stamps = True)

__all__ += ['debian_container_to_vm']
