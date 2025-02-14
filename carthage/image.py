# Copyright (C) 2018, 2019, 2020, 2021, 2024, 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from pathlib import Path
import contextlib
import functools
import os
import os.path
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
class ContainerVolumeImplementation(AsyncInjectable):

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
                await self.clone_from.async_become_ready()
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
            await self.clone_from.async_become_ready()
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



class ContainerVolume( SetupTaskMixin):

    def __init__(self, name, *,
                 clone_from=None,
                 implementation=None,
                 **kwargs):
        super().__init__(**kwargs)
        path = Path(self.config_layout.image_dir).joinpath(name)
        os.makedirs(path.parent, exist_ok=True)
        already_created = path.exists()
        if implementation is None:
            try:
                sh.btrfs(
                    "filesystem", "df", str(path.parent),
                    _bg=False, _async=False)
                implementation = BtrfsVolume
            except sh.ErrorReturnCode:
                implementation = ReflinkVolume
        self.impl = implementation(name=name,
                                   path=path,
                                   injector=self.injector,
                                   config_layout=self.config_layout,
                                   clone_from=clone_from)
        if not already_created:
            self.clear_stamps_and_cache()

    async def async_ready(self):
        await self.impl.async_ready()
        await super().async_ready()

    @property
    def path(self):
        return self.impl.path

    @property
    def name(self):
        return self.impl.name

    @property
    def config_layout(self): 
        return self._config_layout

    @config_layout.setter
    def config_layout(self, cfg):
        self._config_layout = cfg
        try:
            self.impl.config_layout = cfg
        except AttributeError: pass
        return cfg

    @property
    def stamp_subdir(self):
        return f'container_volume/{str(self.impl.path).replace("/","_")}'

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


# We need a mapping because we store qcow2 files as .qcow.
extension_to_qemu_format = dict(
    qcow='qcow2',
    qcow2='qcow2',
    raw='raw',
    vmdk='vmdk',
    )

def btrfs_touch(path:Path):
    # For qcow2 there is an option to set nowcow on btrfs, but not for raw. So to handle both cases we touch and chattr before. chattr may fail, for example if not on btrfs.
    path.touch()
    try:
        sh.chattr('+C', path)
    except sh.ErrorReturnCode: pass

@inject_autokwargs(config_layout=ConfigLayout)
class ImageVolume(SetupTaskMixin, AsyncInjectable):

    '''Represents a file-based disk image.
    Can be initially blank (eros) or can be a copy of another image.
    Supports a populate callback to populate an image before first use.

    :param directory: The directory where unqualified images are
    searched for and where any new files are created (when an image is
    uncompressed for example). Defaults to {vm_image_dir}.

    :param name: The name of the image. It can be either an unqualified image name like ``base_vm`` or a full path name including extension.

    :param base_image: Rather than creating a zero-filled image, create an image as a copy of this file or :class:`ImageVolume`.

    :param size: If the image is not at least this large (MiB), resize it to be that large.


    '''

    base_image: 'str|ImageVolume|DeferredInjection' = None
    preallocate:bool = False
    size:int = 0
    directory:Path = None
    populate = None
    def __init__(self, name=None, directory=None, *,
                 size=None,
                 populate=None,
                 base_image=None,
                 preallocate=None,
                 readonly=None,
                 **kwargs):
        if name is None and not self.name:
            raise TypeError('name must be set on the constructor or subclass')
        super().__init__(**kwargs)
        self.path = None
        if base_image:
            self.base_image = base_image
        if name:
            name = str(name)  # in case it's a Path
            self.name = name
        if readonly is not None:
            self.readonly = readonly
        if directory:
            self.directory = directory
        if self.directory is None:
            self.directory = Path(self.config_layout.vm_image_dir)
            path = self.config_layout.vm_image_dir
        if populate:
            self.populate = functools.partial(populate, self)
        if size:
            self.size = size

    async def async_ready(self):
        # We bypass SetupTaskMixin.async_ready because we want to make
        # sure find is called even when readonly prior to
        # AsyncInjectable.async_ready.
        await self.run_setup_tasks()
        if not self.path: await self.find()
        return await AsyncInjectable.async_ready(self)


    async def find(self):
        if self.path and self.path.exists():
            assert not self.creating_path.exists(), 'Within a single run find should not be called while do_create runs.'
            return True
        name = self.name
        path = self.directory.joinpath(name)
        match list(map(lambda s: s[1:], path.suffixes)):
            case ['raw', 'gz']:
                raise ValueError(f'{path} is supported as a base_image but not an image destination.')
            case [*rest, extension] if extension in extension_to_qemu_format:
                # A single supported image format.
                self.path = path
                self.qemu_format = extension_to_qemu_format[extension]
                self.creating_path = path.with_suffix('.carthage-creating')
                return path.exists() and not self.creating_path.exists()
            case _:
                suffix = path.suffix
                # We need to see if any of our supported extensions exist.
                # If not we pick the default format.
                # This is made more complicated because often images are named after domain names.
                # Let us hope the .qcow2 .raw and .vmdk gtlds do not become popular.
                for extension in extension_to_qemu_format:
                    path = path.with_suffix(suffix+'.'+extension)
                    # with_suffix only strips off one suffix, so clear out any existing suffix
                    suffix = ''
                    if path.exists():
                        self.path = path
                        self.qemu_format = extension_to_qemu_format[extension]
                        self.creating_path = self.path.with_suffix('.carthage-creating')
                        return not self.creating_path.exists()
                # None of the formats exists, so choose our preferred format.
                # We assume that the actual format name is the most canonical extension for that format.
                extension = self.config_layout.libvirt.preferred_format
                self.qemu_format = extension
                path = path.with_suffix('.'+extension)
                assert extension in extension_to_qemu_format, 'Illegal format chosen for preferred format'
                assert not path.exists(), 'We have a logic error and should have found the image in the loop above'
                self.path = path
                self.creating_path = path.with_suffix('.carthage-creating')
                return False

    async def do_create(self):
        if self.readonly:
            raise LookupError('Tried to create but readonly')
        assert self.path
        assert self.qemu_format
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clear_stamps_and_cache()
        try:
            self.creating_path.touch()
            if base_image:= self.base_image:
                if isinstance(base_image, DeferredInjection):
                    await base_image.instantiate_async()
                    base_image = base_image.value
                base_image = await resolve_deferred(self.ainjector, base_image, {})
                if isinstance(base_image, ImageVolume):
                    await base_image.async_become_ready()
                    base_path = base_image.path
                elif isinstance(base_image, (str, Path)):
                    base_path = Path(base_image)
                else:
                    raise TypeError('Do not know what to do with base_image')
                match [s[1:] for s in base_path.suffixes]:
                    case [*rest, 'raw', 'gz']:
                        with tempfile.NamedTemporaryFile(dir=self.directory) as t:
                            await sh.gzip(
                                '-d', '-c',
                                base_path,
                                _out=t.name)
                            await sh.qemu_img(
                                'convert',
                                '-O'+self.qemu_format,
                                '-fraw',
                                t.name,
                                self.path)
                    case [*rest, 'raw'] if self.qemu_format == 'raw' and not self.config_layout.libvirt.use_backing_file:
                        # This is the special case where we are cloning a
                        # raw image, and where we do not want to use a
                        # backing file. In this case we want to use cp
                        # --reflink. You might notice that qemu-img
                        # convert has a -C argument that uses optimized
                        # copies (copy_file_range)and think that ought to
                        # be about the same as cp --reflink. Perhaps it
                        # ought, but with qemu-img 9.1.1 on Linux 6.11.4,
                        # it is not. With xfs, about the first 2G of an
                        # image is reflinked (around the first 1024 or so
                        # calls to copy_file_bytes), and the rest are not shared. On btrfs things appear worse. So we call cp --reflink ourselves in this case.
                        btrfs_touch(self.path)
                        await sh.cp(
                            '--reflink=auto',
                            '-p',
                            base_path,
                            self.path)
                    case [*rest, extension] if extension in extension_to_qemu_format:
                        if self.config_layout.libvirt.use_backing_file:
                            #We want a thin clone
                            btrfs_touch(self.path)
                            await sh.qemu_img(
                                'create',
                                '-b'+str(base_path),
                                '-F'+extension_to_qemu_format[extension],
                                '-f'+self.qemu_format,
                                self.path)
                        else:
                            btrfs_touch(self.path)
                            await sh.qemu_img(
                                'convert',
                                '-C', #Perhaps copy_file_range will actually work in the future
                                '-f'+extension_to_qemu_format[extension],
                                '-O'+self.qemu_format,
                                str(base_path),
                                str(self.path))
                    case _:
                        raise RuntimeError(f'Do not know how to create image {self.path} based on {base_path}')
            else: # No base image
                if not self.size:
                    raise RuntimeError('Cannot create unless size is set')
                btrfs_touch(self.path)
                await sh.qemu_img(
                    'create',
                    '-f'+self.qemu_format,
                    self.path,
                    self.size*1024**2)
            shutil.rmtree(self.stamp_path, ignore_errors=True)
            self.stamp_path.mkdir(parents=True, exist_ok=True)
            if self.populate:
                await self.populate()
            self.creating_path.unlink()
        except Exception:
            try:
                self.path.unlink()
            except FileNotFoundError: pass
            self.creating_path.unlink()
            raise

    async def resize(self, size):
        if self.readonly:
            logger.info('Not resizing %s because readonly', self)
            return
        try:
            await sh.qemu_img(
                'resize',
                '-f'+self.qemu_format,
                str(self.path),
                size*1024**2)
        except sh.ErrorReturnCode_1:
            pass #Tried to shrink

    @property
    def deployable_names(self):
        # find may not be called at this point so path may not be set.
        return [
            'ImageVolume:'+self.name,
            'Volume:'+self.name
            ]
    @setup_task("Find or Create Volume")
    async def find_or_create(self):
        if not await self.find():
            await self.do_create()
        if self.size:
            await self.resize(self.size)

    @find_or_create.check_completed()
    async def find_or_create(self):
        return await self.find()


    def __repr__(self):
        if self.path:
            return f"<{self.__class__.__name__} path={self.path}>"
        return f"<{self.__class__.__name__} name={self.name}>"


    def _delete_volume(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError: pass
        self.clear_stamps_and_cache()

    async def delete(self):
        await self.find()
        return self._delete_volume()

    async def dynamic_dependencies(self):
        if self.base_image and isinstance(self.base_image, ImageVolume):
            return [self.base_image]
        return []

    @property
    def stamp_subdir(self):
        return 'libvirt/'+str(self.path.relative_to('/'))

    def close(self, canceled_futures=None):
        if self.config_layout.delete_volumes:
            self._delete_volume()
        super().close(canceled_futures)

    def __del__(self):
        self.close()

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
        image_mount = await ainjector(ContainerImageMount, self)
        try:
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




class BlockVolume(ImageVolume):

    qemu_format = 'raw'

    def __init__(self, path, **kwargs):
        if 'name' in kwargs:
            raise ValueError('BlockVolume does not take name even though ImageVolume does')
        assert path.startswith('/')
        assert path[1] != '/'  # otherwise stamp_path needs to be more clever
        super().__init__(name=path, unpack=False)

    async def find(self):
        self.path = Path(self.name)
        return self.path.exists()

    async def do_create(self):
        raise NotImplementedError('Cannot create block device')

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

@inject_autokwargs(config_layout=ConfigLayout)
class ContainerImageMount(SetupTaskMixin):

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
            return await super().async_ready()
        except BaseException:
            self.close()
            raise

    def __del__(self):
        self.close()

    @property
    def path(self):
        return self.mount.rootdir


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
        self.image = str(image) if image else image
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
