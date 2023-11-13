# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from pathlib import Path
import contextlib
import os
import os.path
import pkg_resources
import re
import shutil
import sys
import tempfile
import time
from subprocess import check_call, check_output, call
from tempfile import TemporaryDirectory
from .dependency_injection import *
from .config import ConfigLayout, config_key
from . import sh
from .utils import possibly_async, memoproperty
from .setup_tasks import setup_task, SkipSetupTask, SetupTaskMixin, TaskWrapper
import carthage
from .machine import ContainerCustomization, FilesystemCustomization, customization_task


@inject_autokwargs(config_layout=ConfigLayout)
class ContainerVolumeImplementation(AsyncInjectable, SetupTaskMixin):

    def __init__(self, name, path, clone_from=None, **kwargs):
        super().__init__(**kwargs)
        self._path = path
        self._name = name
        self.clone_from = clone_from
        self.closed = False

    @property
    def name(self): return self._name

    @property
    def path(self):
        if self.closed:
            raise RuntimeError("This volume is closed")
        return self._path

    def __del__(self):
        self.close()


@inject_autokwargs(config_layout=ConfigLayout)
class BtrfsVolume(ContainerVolumeImplementation):

    def __repr__(self):
        return "<BtrfsVolume path={}{}>".format(str(self._path),
                                                " closed" if self.closed else "")

    def close(self, canceled_futures=None):
        if self.closed:
            return
        if self.config_layout.delete_volumes:
            subvols = [str(self._path)]
            for root, dirs, files in os.walk(str(self._path), topdown=True):
                device = os.stat(root).st_dev
                for d in dirs:
                    dir = os.path.join(root, d)
                    if os.lstat(dir).st_dev != device:
                        subvols.append(dir)

            for vol in reversed(subvols):
                sh.btrfs('subvolume', 'delete', vol, _bg=False)
        self.closed = True
        super().close(canceled_futures)

    async def async_ready(self):
        try:
            if os.path.exists(self.path):
                try:
                    sh.btrfs("subvolume", "show", str(self.path), _bg=False)
                except sh.ErrorReturnCode:
                    raise RuntimeError("{} is not a btrfs subvolume but already exists".format(self.path))
                # If we're here it is a btrfs subvolume
                return await super().async_ready()
            # directory does not exist
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            if not self.clone_from:
                await sh.btrfs('subvolume', 'create',
                               str(self.path),
                               _bg=True, _bg_exc=False)
            else:
                await sh.btrfs('subvolume', 'snapshot', str(self.clone_from.path), str(self.path),
                               _bg=True, _bg_exc=False)
            return await super().async_ready()
        except BaseException:
            self.close()
            raise


class ReflinkVolume(ContainerVolumeImplementation):

    async def async_ready(self):
        if self.path.exists():
            return await super().async_ready()
        os.makedirs(self.path.parent, exist_ok=True)
        if self.clone_from:
            await sh.cp(
                "-a", "--reflink=auto",
                str(self.clone_from.path),
                self.path,
                _bg=True, _bg_exc=False)
        else:
            os.mkdir(self.path)
        return await super().async_ready()

    def close(self, canceled_futures=None):
        if self.closed:
            return
        try:
            if self.config_layout.delete_volumes:
                shutil.rmtree(self.path)
        except BaseException:
            pass
        self.closed = True
        super().close(canceled_futures)


@inject(config_layout=ConfigLayout)
class ContainerVolume(AsyncInjectable, SetupTaskMixin):

    def __init__(self, name, *,
                 clone_from=None,
                 implementation=None,
                 config_layout,
                 **kwargs):
        super().__init__(**kwargs)
        path = Path(config_layout.image_dir).joinpath(name)
        os.makedirs(path.parent, exist_ok=True)
        if implementation is None:
            try:
                sh.btrfs(
                    "filesystem", "df", str(path.parent),
                    _bg=False)
                implementation = BtrfsVolume
            except sh.ErrorReturnCode:
                implementation = ReflinkVolume
        self.impl = implementation(name=name,
                                   path=path,
                                   injector=self.injector,
                                   config_layout=config_layout,
                                   clone_from=clone_from)

    async def async_ready(self):
        await self.impl.async_ready()
        await self.populate_volume()
        await super().async_ready()

    async def populate_volume(self):
        "Populate the container volume; called for volumes that exist or are empty"
        return await self.run_setup_tasks()

    @property
    def path(self):
        return self.impl.path

    @property
    def name(self):
        return self.impl.name

    @property
    def config_layout(self): return self.impl.config_layout

    @config_layout.setter
    def config_layout(self, cfg):
        self.impl.config_layout = cfg
        return cfg

    @property
    def stamp_path(self): return self.impl.path

    def __repr__(self):
        return f"<Container {self.impl.__class__.__name__} path:{self.impl.path}>"

    def close(self, canceled_futures=None):
        return self.impl.close(canceled_futures)


class ContainerImage(ContainerVolume):

    async def apply_customization(self, cust_class, method='apply', **kwargs):
        from .container import container_image, container_volume, Container
        injector = self.injector(Injector)
        ainjector = injector(AsyncInjector)
        try:
            injector.add_provider(container_image, dependency_quote(self), close=False)
            injector.add_provider(container_volume, dependency_quote(self), close=False)
            container = await ainjector(Container, name=os.path.basename(self.name), skip_ssh_keygen=True, network_config=None)
            customization = await ainjector(cust_class, apply_to=container, **kwargs)
            if hasattr(customization, 'container_args'):
                container.container_args = customization.container_args
            meth = getattr(customization, method)
            return await meth()
        finally:
            try:
                if container.running:
                    await container.stop_machine()
            except Exception:
                pass
        container.close()

    @setup_task('unpack')
    async def unpack_container_image(self):
        await sh.tar('--xattrs-include=*.*', '-xpzf',
                     self.config_layout.base_container_image,
                     _cwd=self.path,
                     _bg_exc=False,
                     _bg=True)

    @setup_task('cleanup-image')
    def cleanup_image(self):
        try:
            os.unlink(os.path.join(self.path, 'usr/sbin/policy-rc.d'))
        except FileNotFoundError:
            pass
        try:
            os.rename(os.path.join(self.path, "sbin/init.dist"),
                      os.path.join(self.path, "sbin/init"))
        except FileNotFoundError:
            pass


def wrap_container_customization(task: TaskWrapper, **kwargs):
    '''
Takes a :func:`setup_task` and wraps it in a :class:`ContainerCustomization` so that it can be used in a :class:`ContainerVolume`.  Consider the following::

        @setup_task("frob the filesystem")
        async def frob_filesystem(self):
            async with self.filesystem_access() as path: # ...

    Such a setup task cannot be directly applied to a :class:`ContainerVolume` because ContainerVolume does not have *filesystem_access* or any of the other customization methods.  *wrap_container_customization* wraps such a setup_task in a :class:`ContainerCustomization` so that it can be applied to a volume.
    '''
    class cust(ContainerCustomization):
        description = task.description
        task_to_run = task
    return carthage.machine.customization_task(cust, **kwargs)


@inject_autokwargs(config_layout=ConfigLayout)
class ImageVolume(AsyncInjectable, SetupTaskMixin):

    def __init__(self, name, path=None, create_size=None,
                 unpack=None,
                 remove_stamps=False,
                 **kwargs):
        super().__init__(**kwargs)
        name = str(name)  # in case it's a Path
        if path is None:
            path = self.config_layout.vm_image_dir
        self.name = name
        if name.startswith('/'):
            self.path = name
        else:
            self.path = os.path.join(path, name + '.raw')

        if not os.path.exists(self.path):
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            remove_stamps = True
            if create_size:
                with open(self.path, "w") as f:
                    try:
                        sh.chattr("+C", self.path, _bg=False)
                    except sh.ErrorReturnCode_1:
                        pass
            self.create_size = create_size
        if remove_stamps:
            shutil.rmtree(self.stamp_path, ignore_errors=True)
        os.makedirs(self.stamp_path, exist_ok=True)
        self._pass_self_to_unpack = False
        if callable(unpack):
            self._pass_self_to_unpack = True
            self.unpack = unpack

    def __repr__(self):
        return f"<{self.__class__.__name__} path={self.path}>"

    async def async_ready(self):
        await self.run_setup_tasks()
        return await super().async_ready()

    def delete_volume(self):
        try:
            os.unlink(self.path)
            shutil.rmtree(self.stamp_path)
        except FileNotFoundError:
            pass

    @property
    def stamp_path(self):
        return Path(str(self.path) + '.stamps')

    def close(self, canceled_futures=None):
        if self.config_layout.delete_volumes:
            self.delete_volume()
        super().close(canceled_futures)

    def __del__(self):
        self.close()

    async def unpack(self):
        """Override this method in a subclass to implement alternate creation methods
            Alternatively specify unpack=func() in the constructor
        """
        if self.create_size is None:
            raise RuntimeError(f"{self.path} not found and creation disabled")
        await sh.gzip('-d', '-c',
                      self.config_layout.base_vm_image,
                      _out=self.path,
                      _no_pipe=True,
                      _no_out=True,
                      _out_bufsize=1024 * 1024,
                      _tty_out=False,
                      _bg=True,
                      _bg_exc=False)
        if hasattr(self, 'create_size'):
            os.truncate(self.path, self.create_size)

    @setup_task('unpack')
    async def unpack_installed_system(self):
        if os.path.exists(self.path):
            return  # mark as succeeded rather than skipped

        # We never want to unpack later after we've decided not to
        if self._pass_self_to_unpack:
            return await self.unpack(self)
        return await self.unpack()

    def qemu_config(self, disk_config):
        return dict(
            path=self.path,
            source_type="file",
            driver='raw',
            qemu_source='file',
        )

    async def apply_customization(self, cust_class, method='apply', **kwargs):
        from .container import container_image, container_volume, Container
        injector = self.injector(Injector)
        ainjector = injector(AsyncInjector)
        try:
            image_mount = await ainjector(ContainerImageMount, self)
            injector.add_provider(container_image, image_mount)
            injector.add_provider(container_volume, image_mount)
            container = await ainjector(Container, name=os.path.basename(self.name), skip_ssh_keygen=True, network_config=None)
            customization = await ainjector(cust_class, apply_to=container, **kwargs)
            if hasattr(customization, 'container_args'):
                container.container_args = customization.container_args
            meth = getattr(customization, method)
            return await meth()
        finally:
            try:
                if container.running:
                    await container.stop_machine()
            except Exception:
                pass
            image_mount.close()

    @contextlib.contextmanager
    def image_mounted(self):
        with image_mounted(self.path, mount=False) as i:
            with TemporaryDirectory() as d:
                sh.mount('-osubvol=@',
                         i.rootdev, d, _bg=False)
                i.rootdir = d
                try:
                    yield i
                finally:
                    for i in range(5):
                        try:
                            sh.sync(_bg=False)
                            sh.umount(d,_bg=False)
                            sh.sync(_bg=False)
                            time.sleep(5)
                            break
                        except sh.ErrorReturnCode as e:
                            if 'busy' in e.stderr.lower():
                                time.sleep(0.5)
                            else:
                                raise

    def clone_for_vm(self, name, *,
                     path=None, volume_type='qcow'):
        kwargs = {}
        if path is not None:
            kwargs['path'] = path
        if volume_type == 'qcow':
            return self.injector(QcowCloneVolume, name, self, **kwargs)
        elif volume_type == 'raw':
            return self.injector(ImageVolume, name=name, image_base=self.path, **kwargs)
        else:
            raise ValueError("Unknown volume type {}".format(volume_type))


class BlockVolume(ImageVolume):

    def __init__(self, path, **kwargs):
        if 'name' in kwargs:
            raise ValueError('BlockVolume does not take name even though ImageVolume does')
        assert path.startswith('/')
        assert path[1] != '/'  # otherwise stamp_path needs to be more clever
        super().__init__(name=path, unpack=False)

    @memoproperty
    def stamp_path(self):
        res = Path(self.config_layout.state_dir) / "block_volume_stamps" / self.path[1:]
        os.makedirs(res, exist_ok=True)
        return res

    def qemu_config(self, disk_config):
        res = dict(
            source_type='block',
            path=self.path,
            driver='raw',
            qemu_source='dev')
        return res


@inject_autokwargs(
    config_layout=ConfigLayout,
)
class QcowCloneVolume(Injectable):

    def __init__(self, name, volume, **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.path = os.path.join(self.config_layout.vm_image_dir, name + ".qcow")
        if not os.path.exists(self.path):
            sh.qemu_img(
                'create', '-fqcow2',
                '-obacking_file=' + volume.path,
                '-obacking_fmt='+volume.qemu_config(dict())['driver'],
                str(self.path),_bg=False)

    def delete_volume(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass

    def close(self, canceled_futures=None):
        if self.config_layout.delete_volumes:
            self.delete_volume()

    def qemu_config(self, disk_config):
        return dict(
            path=self.path,
            source_type='file',
            driver='qcow2',
            qemu_source='file'
        )


@inject_autokwargs(config_layout=ConfigLayout)
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
        except AttributeError:
            pass

    def close(self):
        self.unmount_image()
        try:
            del self.image
        except AttributeError:
            pass

    async def async_ready(self):
        try:
            await self.run_setup_tasks()
            return await super().async_ready()
        except BaseException:
            self.close()
            raise

    def __del__(self):
        self.close()

    @property
    def path(self):
        return self.mount.rootdir


@inject(
    ainjector=AsyncInjector,
    config_layout=ConfigLayout)
def image_factory(name, image_type='raw',
                  image=ImageVolume, *,
                  config_layout, ainjector):
    assert image_type == 'raw'
    path = os.path.join(config_layout.vm_image_dir, name + '.raw')
    return ainjector(image, name=name, path=path,
                     create_size=config_layout.vm_image_size)


class SshAuthorizedKeyCustomizations(FilesystemCustomization):

    description = "Set up authorized_keys file"

    @setup_task('Copy in hadron-operations ssh authorized_keys')
    @inject(authorized_keys=carthage.ssh.AuthorizedKeysFile)
    def add_authorized_keys(self, authorized_keys):
        os.makedirs(os.path.join(self.path, "root/.ssh"), exist_ok=True)
        shutil.copy2(authorized_keys.path,
                     os.path.join(self.path, 'root/.ssh/authorized_keys'))


class image_mounted(object):

    '''A contextmanager for mounting and image'''

    def __init__(self, image=None, mount=True, extra_dirs=[],
                 subvol="@", rootdir=None):
        self.image = image
        self.mount = mount
        self.mount_dirs = "dev proc sys dev/pts".split()
        self.mount_dirs.extend(extra_dirs)
        self.rootdir = rootdir
        if rootdir and image:
            raise RuntimeError("Do not specify both image and rootdir")
        self.loopdev = None
        self.subvol = subvol

    def __enter__(self):
        if self.image:
            self.clear_existing_loops(self.image)
            if self.image.startswith('/dev'):
                # This technically won't work for an lvm image unless the
                # calling user calls kpartx themselves, but it will work
                # for an actual disk.  Detecting the LVM case is kind of
                # tricky.
                if os.path.exists(self.image + '2'):
                    self.rootdev = self.image + '2'
                else:
                    self.rootdev = self.image
            else:
                mappings = str(check_output(['kpartx',
                                             '-asv', self.image]), 'utf8').split("\n")
                partitions = list(map(lambda x: x.split()[2], filter(lambda x: x, mappings)))
                self.loopdev = re.match(r'^(loop[0-9]+)', partitions[0]).group(1)
                self.loopdev = "/dev/" + self.loopdev
                partitions = list(map(lambda x: "/dev/mapper/" + x, partitions))
                self.rootdev = partitions[1]
        try:
            self.rootdir = tempfile.mkdtemp()
            if self.mount:
                options = []
                if self.subvol:
                    options.append('-osubvol=' + self.subvol)
                check_call(['mount'] + options + [self.rootdev, self.rootdir])
                for dir in self.mount_dirs:
                    check_call(['mount', '-obind', '/' + dir,
                                os.path.join(self.rootdir, dir)])
        except Exception as e:
            self.__exit__(type(e), e, None)
            raise e
        return self

    def clear_existing_loops(self, image):
        lines = str(check_output(['losetup', '-j', image]), 'utf-8').split("\n")
        if not lines:
            return
        for line in lines:
            (loopdev, *rest) = line.split(':')
            if not loopdev.startswith('/dev'):
                continue
            call(['kpartx', '-ds', loopdev])
            call(['losetup', '-d', loopdev])
        call(['kpartx', '-d', self.image])

    def __exit__(self, *excinfo):
        if self.rootdir and self.mount:
            for dir in reversed(self.mount_dirs):
                call(['umount',
                      os.path.join(self.rootdir, dir)])
            call(['umount', self.rootdir])
            os.rmdir(self.rootdir)
        if self.loopdev:
            check_call(['kpartx', '-d', self.loopdev])
            call(['losetup', '-d', self.loopdev])
        return False

    def chroot(self, command, *args):
        if isinstance(command, str):
            command = [command]
        command.extend(args)
        command = ['chroot', self.rootdir] + command
        check_call(command)


__all__ = ('ContainerVolume', 'ContainerImage',
           "wrap_container_customization",
           'SetupTaskMixin',
           'SkipSetupTask',
           'ImageVolume',
           'SshAuthorizedKeyCustomizations')
