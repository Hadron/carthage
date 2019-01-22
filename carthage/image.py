import contextlib, os, os.path, shutil, sys, time
from tempfile import TemporaryDirectory
from .dependency_injection import Injector, AsyncInjectable, inject, AsyncInjector
from .config import ConfigLayout
from . import sh
from .utils import possibly_async
from .setup_tasks import setup_task, SkipSetupTask, SetupTaskMixin
import carthage






@inject(config_layout = ConfigLayout)
class BtrfsVolume(AsyncInjectable, SetupTaskMixin):

    def __init__(self, config_layout, name, clone_from = None):
        super().__init__()
        self.config_layout = config_layout
        self._name = name
        self._path = os.path.join(config_layout.image_dir, name)
        self.clone_from = clone_from

    @property
    def name(self): return self._name

    @property
    def path(self):
        if self._path is None:
            raise RuntimeError("This volume is closed")
        return self._path

    def __repr__(self):
        return "<BtrfsVolume path={}>".format(self.path)

    def close(self):
        if self._path is None: return
        if self.config_layout.delete_volumes:
            subvols = [self._path]
            for root,dirs, files in os.walk(self._path, topdown = True):
                device = os.stat(root).st_dev
                for d in dirs:
                    dir = os.path.join(root, d)
                    if os.lstat(dir).st_dev != device: subvols.append(dir)

            for vol in reversed(subvols):
                sh.btrfs('subvolume', 'delete', vol, )
        self._path = None

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
                return self
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
            return self
        except:
            self.close()
            raise


    async def populate_volume(self):
        "Populate a new volume; called both for cloned and non-cloned volumes"
        await self.run_setup_tasks()
    stamp_path = path




@inject(config_layout = ConfigLayout)
class ContainerImage(BtrfsVolume):

    def __init__(self, name, config_layout):
        super().__init__(config_layout = config_layout, name = name)

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





@inject(
    config_layout = ConfigLayout,
ainjector = AsyncInjector)
class ImageVolume(AsyncInjectable, SetupTaskMixin):

    def __init__(self, name, path, create_size = None,
                 *, config_layout, ainjector):
        self.config_layout = config_layout
        self.ainjector = ainjector
        self.injector = ainjector.injector.claim()
        super().__init__()
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


    async def async_ready(self):
        await self.run_setup_tasks()
        return self

    def delete_volume(self):
        try:
            os.unlink(self.path)
            shutil.rmtree(self.stamp_path)
        except FileNotFoundError: pass

    @property
    def stamp_path(self):
        return self.path+'.stamps'

    def close(self):
        if self.config_layout.delete_volumes:
            self.delete_volume()

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

    @contextlib.contextmanager
    def image_mounted(self):
        from hadron.allspark.imagelib import image_mounted
        with image_mounted(self.path, mount = False) as i:
            with TemporaryDirectory() as d:
                sh.mount('-osubvol=@',
                         i.rootdev, d)
                i.rootdir = d
                yield i
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

    def close(self):
        if self.config_layout.delete_volumes:
            self.delete_volume()

@inject(config_layout = ConfigLayout,
        ainjector = AsyncInjector)
class ContainerImageMount(AsyncInjectable, SetupTaskMixin):

    '''Mount a disk image for use as a container_image or
    container_volume.  Note that this works for LVM and raw images, but
    not currently for qcow2 images.  If you use this as a
    container_volume, you must call close yourself as the container will
    not.
    '''

    def __init__(self, image,
                 *, config_layout, ainjector):
        super().__init__()
        self.image = image
        self.config_layout = config_layout
        self.injector = ainjector.injector.copy_if_owned().claim()
        self.name = image.name
        self.mount_image()
        self.stamp_path = image.stamp_path

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
            return self
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
def image_factory(name, image_type = 'raw', *,
                  config_layout, ainjector):
    assert image_type == 'raw'
    path = os.path.join(config_layout.vm_image_dir, name+'.raw')
    return ainjector(ImageVolume, name = name, path = path,
                     create_size = config_layout.vm_image_size)
__all__ = ('BtrfsVolume', 'ContainerImage', 'SetupTaskMixin',
           'SkipSetupTask',
           'ImageVolume')
