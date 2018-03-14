# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import contextlib, os, os.path, shutil, sys, time
from tempfile import TemporaryDirectory
from .dependency_injection import Injector, AsyncInjectable, inject, AsyncInjector
from .config import ConfigLayout
from . import sh
from .utils import possibly_async
import carthage


_task_order = 0
def setup_task(stamp):
    '''Mark a method as a setup task.  Indicate a stamp file to be created
    when the operation succeeds.  Must be in a class that is a subclass of
    SetupTaskMixin.  Usage:

        @setup_task("unpack"
        async def unpack(self): ...
    '''
    def wrap(fn):
        global _task_order
        fn._setup_task_info = (_task_order, stamp)
        _task_order += 1
        return fn
    return wrap

class SkipSetupTask(Exception): pass


class SetupTaskMixin:

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_tasks = sorted(self._class_setup_tasks(),
                                  key = lambda t: t[1]._setup_task_info[0])

    def add_setup_task(self, stamp, task):
        self.setup_tasks.append((stamp, task))

    async def run_setup_tasks(self, context = None):
        '''Run the set of collected setup tasks.  If context is provided, it
        is used as an asynchronous context manager that will be entered before the
        first task and eventually exited.  The context is never
        entered if no tasks are run.
        '''
        injector = getattr(self, 'injector', carthage.base_injector)
        ainjector = getattr(self, 'ainjector', None)
        if ainjector is None:
            ainjector = injector(AsyncInjector)
        context_entered = False
        for stamp, task in self.setup_tasks:
            if not check_stamp(self.stamp_path, stamp):
                try:
                    if (not context_entered) and context is not None:
                        await context.__aenter__()
                        context_entered = True
                    await ainjector(task)
                    create_stamp(self.stamp_path, stamp)
                except SkipSetupTask: pass
                except Exception:
                    if context_entered:
                        await context.__aexit(*sys.exc_info())
                    raise
        if context_entered:
            await context.__aexit__(None, None, None)

    def _class_setup_tasks(self):
        cls = self.__class__
        meth_names = {}
        for c in cls.__mro__:
            if not issubclass(c, SetupTaskMixin): continue
            for m in c.__dict__:
                if m in meth_names: continue
                meth = getattr(c, m)
                meth_names[m] = True
                if hasattr(meth, '_setup_task_info'):
                    yield meth._setup_task_info[1], getattr(self, m)



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
            sh.btrfs('subvolume', 'delete', self.path, )
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


def create_stamp(path, stamp):
    with open(os.path.join(path, ".stamp-"+stamp), "w") as f:
        pass


def check_stamp(path, stamp, raise_on_error = False):
    if not os.path.exists(os.path.join(path,
                                       ".stamp-"+stamp)):
        if raise_on_error: raise RuntimeError("Stamp not available")
        return False
    return True


__all__ = ('BtrfsVolume', 'ContainerImage', 'SetupTaskMixin',
           'SkipSetupTask')

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
        self.path = path
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok = True)
            if create_size is None:
                raise RuntimeError("{} not found and creation disabled".format(path))
            with open(path, "w") as f:
                try: sh.chattr("+C", path)
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
                        
                            

    def clone_for_vm(self, name):
        return self.injector(QcowCloneVolume, name, self)


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
        self.mount_context = image.image_mounted()
        self.mount = self.mount_context.__enter__()
        self.stamp_path = image.stamp_path

    def close(self):
        if hasattr(self, 'mount_context'):
            self.mount_context.__exit__(None, None, None)
            del self.mount
            del self.mount_context
            del self.image

    async def async_ready(self):
        await self.run_setup_tasks()
        return self
    
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
