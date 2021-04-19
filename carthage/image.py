import contextlib, os, os.path, pkg_resources, shutil, sys, time
from tempfile import TemporaryDirectory
from .dependency_injection import *
from .config import ConfigLayout
from . import sh
from .utils import possibly_async
from .setup_tasks import setup_task, SkipSetupTask, SetupTaskMixin
import carthage
from .machine import ContainerCustomization, customization_task





@inject(config_layout = ConfigLayout)
class BtrfsVolume(AsyncInjectable, SetupTaskMixin):

    def __init__(self, config_layout, name, clone_from = None, **kwargs):
        super().__init__(**kwargs)
        self.config_layout = config_layout
        self._name = name
        self._path = os.path.join(config_layout.image_dir, name)
        self.clone_from = clone_from
        self.closed = False

    @property
    def name(self): return self._name

    @property
    def path(self):
        if self.closed:
            raise RuntimeError("This volume is closed")
        return self._path

    def __repr__(self):
        return "<BtrfsVolume path={}{}>".format(self._path,
                                                 " closed" if self.closed else "")

    def close(self, canceled_futures = None):
        if self.closed: return
        if self.config_layout.delete_volumes:
            subvols = [self._path]
            for root,dirs, files in os.walk(self._path, topdown = True):
                device = os.stat(root).st_dev
                for d in dirs:
                    dir = os.path.join(root, d)
                    if os.lstat(dir).st_dev != device: subvols.append(dir)

            for vol in reversed(subvols):
                sh.btrfs('subvolume', 'delete', vol, )
        self.closed = True
        super().close(canceled_futures)


    def __del__(self):
        self.close()

    async def async_ready(self):
        try:
            if os.path.exists(self.path):
                try: sh.btrfs("subvolume", "show", self.path)
                except sh.ErrorReturnCode:
                    raise RuntimeError("{} is not a btrfs subvolume but already exists".format(self.path))
                # If we're here it is a btrfs subvolume
                await possibly_async(self.populate_volume())
                return await super().async_ready()
            # directory does not exist
            os.makedirs(os.path.dirname(self.path), exist_ok = True)
            if not self.clone_from:
                await sh.btrfs('subvolume', 'create',
                               self.path,
                               _bg = True, _bg_exc = False)
            else:
                await sh.btrfs('subvolume', 'snapshot', self.clone_from.path, self.path,
                               _bg = True, _bg_exc = False)
            await possibly_async(self.populate_volume())
            return await super().async_ready()
        except:
            self.close()
            raise


    async def populate_volume(self):
        "Populate a new volume; called both for cloned and non-cloned volumes"
        await self.run_setup_tasks()
    stamp_path = path




@inject(config_layout = ConfigLayout)
class ContainerImage(BtrfsVolume):

    def __init__(self, name, config_layout, **kwargs):
        super().__init__(config_layout = config_layout, name = name, **kwargs)

    async def apply_customization(self, cust_class, method = 'apply'):
        from .container import container_image, container_volume, Container
        injector = self.injector(Injector)
        ainjector = injector(AsyncInjector)
        try:
            injector.add_provider(container_image, dependency_quote(self), close = False)
            injector.add_provider(container_volume, dependency_quote(self), close = False)
            container = await ainjector(Container, name = self.name, skip_ssh_keygen = True)
            customization = await ainjector(cust_class, apply_to = container)
            meth = getattr(customization, method)
            return await meth()
        finally:
            try:
                if container.running:
                    await container.stop_machine()
            except Exception: pass
        container.close()

    @setup_task('unpack')
    async def unpack_container_image(self):
        await sh.tar('--xattrs-include=*.*', '-xpzf',
                     self.config_layout.base_container_image,
                     _cwd = self.path,
                     _bg_exc = False,
                     _bg = True)

    @setup_task('cleanup-image')
    def cleanup_image(self):
        try:         os.unlink(os.path.join(self.path, 'usr/sbin/policy-rc.d'))
        except FileNotFoundError: pass
        try: os.rename(os.path.join(self.path, "sbin/init.dist"),
                  os.path.join(self.path, "sbin/init"))
        except FileNotFoundError: pass

class DebianContainerCustomizations(ContainerCustomization):

    description = "Set up Debian for Carthage"
    
    @setup_task("Turn on networkd")
    async def turn_on_networkd(self):
        await self.container_command("systemctl", "enable", "systemd-networkd")


class DebianContainerImage(ContainerImage):

    mirror: str = "https://deb.debian.org/debian"
    distribution: str = "bullseye"

    def __init__(self, name:str = "base-debian",
                 mirror: str = None, distribution: str = None, **kwargs):
        if mirror: self.mirror = mirror
        if distribution: self.distribution = distribution
        super().__init__(name, **kwargs)

    @setup_task("unpack")
    async def unpack_container_image(self):
        await sh.debootstrap('--include=openssh-server',
                             self.distribution,
                             self.path, self.mirror,
                             _bg = True,
                             _bg_exc = False)

    debian_customizations = customization_task(DebianContainerCustomizations)
    




@inject_autokwargs(config_layout = ConfigLayout)
class ImageVolume(AsyncInjectable, SetupTaskMixin):

    def __init__(self, name, path = None, create_size = None,
                 **kwargs):
        super().__init__(**kwargs)
        if path is None: path = self.config_layout.vm_image_dir
        self.name = name
        if name.startswith('/'):
            self.path = name
        else: self.path = os.path.join(path, name+'.raw')

        if not os.path.exists(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok = True)
            if create_size is None:
                raise RuntimeError("{} not found and creation disabled".format(path))
            with open(self.path, "w") as f:
                try: sh.chattr("+C", self.path)
                except sh.ErrorReturnCode_1: pass
                self.create_size = create_size
                os.makedirs(self.stamp_path)

    def __repr__(self):
        return f"<ImageVolume path={self.path}>"


    async def async_ready(self):
        await self.run_setup_tasks()
        return await super().async_ready()

    def delete_volume(self):
        try:
            os.unlink(self.path)
            shutil.rmtree(self.stamp_path)
        except FileNotFoundError: pass

    @property
    def stamp_path(self):
        return self.path+'.stamps'

    def close(self, canceled_futures = None):
        if self.config_layout.delete_volumes:
            self.delete_volume()
        super().close(canceled_futures)

    def __del__(self):
        self.close()

    @setup_task('unpack')
    async def unpack_installed_system(self):
        await sh.gzip('-d', '-c',
                      self.config_layout.base_vm_image,
                      _out = self.path,
                      _no_pipe = True,
                      _no_out = True,
                      _out_bufsize = 1024*1024,
                      _tty_out = False,
                      _bg = True,
                      _bg_exc = False)
        if hasattr(self, 'create_size'):
            os.truncate(self.path, self.create_size)

    async def apply_customization(self, cust_class, method = 'apply'):
        from .container import container_image, container_volume, Container
        injector = self.injector(Injector)
        ainjector = injector(AsyncInjector)
        try:
            image_mount = await ainjector(ContainerImageMount, self)
            injector.add_provider(container_image, image_mount)
            injector.add_provider(container_volume, image_mount)
            container = await ainjector(Container, name = self.name, skip_ssh_keygen = True)
            customization = await ainjector(cust_class, apply_to = container)
            meth = getattr(customization, method)
            return await meth()
        finally:
            try:
                if container.running:
                    await container.stop_machine()
            except Exception: pass
            image_mount.close()


    @contextlib.contextmanager
    def image_mounted(self):
        from hadron.allspark.imagelib import image_mounted
        with image_mounted(self.path, mount = False) as i:
            with TemporaryDirectory() as d:
                sh.mount('-osubvol=@',
                         i.rootdev, d)
                i.rootdir = d
                try:
                    yield i
                finally:
                    for i in range(5):
                        try:
                            sh.sync()
                            sh.umount(d)
                            sh.sync()
                            time.sleep(5)
                            break
                        except sh.ErrorReturnCode as e:
                            if 'busy' in e.stderr.lower():
                                time.sleep(0.5)
                            else: raise



    def clone_for_vm(self, name, *,
                     path = None, volume_type = 'qcow'):
        kwargs = {}
        if path is not None: kwargs['path'] = path
        if volume_type == 'qcow':
            return self.injector(QcowCloneVolume, name, self, **kwargs)
        elif volume_type == 'raw':
            return self.injector(ImageVolume, name = name, image_base = self.path, **kwargs)
        else:
            raise ValueError("Unknown volume type {}".format(volume_type))



@inject(
    config_layout = ConfigLayout,
    ainjector = AsyncInjector)
class QcowCloneVolume:

    def __init__(self, name, volume,
                 *, config_layout, ainjector):
        self.ainjector = ainjector
        self.injector = ainjector.injector
        self.name = name
        self.path = os.path.join(config_layout.vm_image_dir, name+".qcow")
        if not os.path.exists(self.path):
            sh.qemu_img(
                'create', '-fqcow2',
                '-obacking_file='+volume.path,
self.path)
        self.config_layout = config_layout


    def delete_volume(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError: pass

    def close(self, canceled_futures = None):
        if self.config_layout.delete_volumes:
            self.delete_volume()
        super().close(canceled_futures)

@inject_autokwargs(config_layout = ConfigLayout)
class ContainerImageMount(AsyncInjectable, SetupTaskMixin):

    '''Mount a disk image for use as a container_image or
    container_volume.  Note that this works for LVM and raw images, but
    not currently for qcow2 images.  If you use this as a
    container_volume, you must call close yourself as the container will
    not.
    '''

    def __init__(self, image,
                 **kwargs):
        super().__init__(**kwargs)
        self.image = image

        self.name = image.name
        self.mount_image()
        self.stamp_path = image.stamp_path

        def __repr__(self):
            return f"<{self.__class__.__name__} for {repr(self.image)}>"

    def mount_image(self):
        self.mount_context = self.image.image_mounted()
        self.mount = self.mount_context.__enter__()

    def unmount_image(self):
        try:
            mount_context = self.mount_context
            del self.mount_context
            del self.mount
            mount_context.__exit__(None, None, None)
        except AttributeError: pass

    def close(self):
        self.unmount_image()
        try:
            del self.image
        except AttributeError: pass

    async def async_ready(self):
        try:
            await self.run_setup_tasks()
            return await super().async_ready()
        except:
            self.close()
            raise

    def __del__(self):
        self.close()

    @property
    def path(self):
        return self.mount.rootdir





@inject(
    ainjector = AsyncInjector,
    config_layout = ConfigLayout)
def image_factory(name, image_type = 'raw',
                  image = ImageVolume, *,
                  config_layout, ainjector):
    assert image_type == 'raw'
    path = os.path.join(config_layout.vm_image_dir, name+'.raw')
    return ainjector(image, name = name, path = path,
                     create_size = config_layout.vm_image_size)

class SshAuthorizedKeyCustomizations(ContainerCustomization):

    description = "Set up authorized_keys file"

    @setup_task('Copy in hadron-operations ssh authorized_keys')
    @inject(authorized_keys = carthage.ssh.AuthorizedKeysFile)
    def add_authorized_keys(self, authorized_keys):
        os.makedirs(os.path.join(self.path, "root/.ssh"), exist_ok = True)
        shutil.copy2(authorized_keys.path,
                     os.path.join(self.path, 'root/.ssh/authorized_keys'))

__all__ = ('BtrfsVolume', 'ContainerImage', 'DebianContainerImage',
           'SetupTaskMixin',
           'SkipSetupTask',
           'ImageVolume',
           'SshAuthorizedKeyCustomizations')
