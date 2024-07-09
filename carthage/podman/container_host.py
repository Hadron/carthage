# Copyright (C)  2022, 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
from __future__ import annotations
import asyncio
import contextlib
import datetime
import json
import logging
import os
from pathlib import Path
import tempfile
import shutil
import shlex
import uuid
import dateutil.parser
import carthage.machine
from carthage.dependency_injection import *
from .. import sh, ConfigLayout, become_privileged, deployment
from ..machine import AbstractMachineModel, Machine
from ..utils import memoproperty
from ..oci import *

__all__ = []

logger = logging.getLogger('carthage.podman')

CARTHAGE_SOCKET_DIRECTORY = Path('/var/lib/carthage/podman_sockets')

class PodmanContainerHost(AsyncInjectable):

    @memoproperty
    def podman_log(self):
        return self.injector.get_instance(InjectionKey("podman_log", _optional=True))

    def podman(self, *args,
               _bg=True, _bg_exc=True):
        raise NotImplementedError

    def podman_nosocket(self, *args, **kwargs):
        '''Run podman directly on the container host rather than
        using a podman socket.  Used for example for container commits
        because for example podman 4.9 does not appear to understand
        the commit results from podman 4.6.
        '''
        return self.podman(*args, **kwargs)
    
    async def filesystem_access(self, *args):

        '''Gain filesystem access to a podman resource.  Arguments are
        passed to podman; for things to work needs to be a container
        or volume mount.
        '''
        raise NotImplementedError

    async def tar_volume_context(self, volume):

        '''
        An asynchronous context manager that tars up a volume and provides a path to that tar file usable in ``podman import``.  Typical usage::

            async with container_host.tar_volume_context(container_image) as path:
                await container_host.podman('import', path)

        On local systems this manages temporary directories.  For remote container hosts, this manages to get the tar file to the remote system and clean up later.
        '''
        raise NotImplementedError

    async def find(self):
        '''Return true if find_deployable is true  for the underlying container host.
        '''
        return True
    
    async def start_container_host(self):
        pass

    @property
    def extra_args(self):
        '''Extra arguments to pass to podman from ansible plugin.
        '''
        return ''

class LocalPodmanContainerHost(PodmanContainerHost):



    @contextlib.asynccontextmanager
    async def filesystem_access(self, *args):
        result = await self.podman(
*args,
            _bg=True, _bg_exc=False, _log=False)
        try:
            path = str(result).strip()
            yield Path(path)
        finally:
            pass  # Perhaps we should unmount, but we'd need a refcount to do that.

    async def podman(self, *args,
               _bg=True, _bg_exc=False, _log=True, _fg=False):
        options = {}
        if _log and self.podman_log:
            options['_out']=str(self.podman_log)
            options['_err_to_out'] = True
        result = sh.podman(
            *args,
            _fg=_fg,
            **options)
        if not _fg:
            await result
            return result

    @contextlib.asynccontextmanager
    async def tar_volume_context(self, volume):
        assert hasattr(volume, 'path')
        with tempfile.TemporaryDirectory() as path_raw:
            path = Path(path_raw)
            await sh.tar(
                "-C", str(volume.path),
                "--xattrs",
                "--xattrs-include=*.*",
                "-czf",
                str(path / "container.tar.gz"),
                ".",
                _bg=True,
                _bg_exc=False)
            yield path / 'container.tar.gz'

__all__ += ['LocalPodmanContainerHost']

class RemotePodmanHost(PodmanContainerHost):

    machine: Machine = None
    user: str = None

    def __init__(self, machine, user=None, **kwargs):
        super().__init__(**kwargs)
        self.machine = machine
        if user is None:
            user = machine.runas_user
        self.user = user
        self._operation_lock = asyncio.Lock()
        self.process = None
        self.local_socket = None
        self.sshfs_count = 0
        self.sshfs_path = None
        self.sshfs_process = None
        self.sshfs_lock = asyncio.Lock()


    def __repr__(self):
        try:
            return f'<PodmanContainerHost on {self.machine.name}>'
        except Exception:
            return '<PodmanContainerHost>'

    async def find(self):
        '''
        Return True if self.machine is found as a deployable.
        '''
        return await deployment.find_deployable(self.machine)
    
    async def start_container_host(self):
        machine = self.machine
        if self.local_socket:
            return
        async with self._operation_lock:
            await machine.async_become_ready()
            await machine.start_machine()
            await machine.ssh_online()
            become_privileged_command = []
            if hasattr(machine, 'become_privileged_command'):
                become_privileged_command = machine.become_privileged_command(self.user)
            #xxx we should probe for home directory
            if self.user == 'root':
                home_directory = '/root'
            else:
                home_directory = '/home/'+self.user
            if become_privileged_command:
                assert ':' not in machine.ssh_login_user
                await machine.run_command(
                    'mkdir',
                    '-m755',
                    '-p',
                    CARTHAGE_SOCKET_DIRECTORY,
                    _user='root')
                await machine.run_command(
                    'mkdir',
                    '-m711',
                    '-p',
                    CARTHAGE_SOCKET_DIRECTORY/self.user,
                    _user='root')
                await machine.run_command(
                    'chown', self.user,
                    CARTHAGE_SOCKET_DIRECTORY/self.user,
                    _user='root')
                await machine.run_command(
                    'mkdir',
                    '-m700',
                    '-p',
                    CARTHAGE_SOCKET_DIRECTORY/self.user/machine.ssh_login_user,
                    _user=self.user)
                await machine.run_command(
                    'setfacl', '-m',
                    f'default:user:{machine.ssh_login_user}:rwx,user:{machine.ssh_login_user}:rwx',
                    CARTHAGE_SOCKET_DIRECTORY/self.user/machine.ssh_login_user,
                    _user=self.user)
                socket_directory = CARTHAGE_SOCKET_DIRECTORY/self.user/machine.ssh_login_user
            else:
                socket_directory = home_directory+'/.carthage/podman_sockets'
                await machine.run_command(
                    'mkdir', '-p', socket_directory,
                    _user=self.user)
            socket = str(socket_directory)+'/'+str(uuid.uuid4())
            config = machine.injector(ConfigLayout)
            state_dir = Path(config.state_dir)
            local_socket = state_dir/'local_podman_sockets'/machine.name
            local_socket.parent.mkdir(exist_ok=True, parents=True)
            with contextlib.suppress(OSError):
                local_socket.unlink()
            self.process = machine.ssh(
                f'-L{local_socket}:{socket}',
                *become_privileged_command,
            'podman', 'system', 'service',
                '--timeout', '90',
                f'unix://{socket}',
                _out=self.out_cb,
                _err_to_out=True,
                _bg=True, _bg_exc=False,
                _done=self.process_done)
            logger.debug('%r waiting for podman socket', self)
            for i in range(5):
                try:
                    if become_privileged_command:
                        await machine.run_command(
                            'chmod', 'g+rw',
                            socket, _user=self.user)
                    await sh.podman(
                        '--url=unix://'+str(local_socket),
                        'info')
                    logger.info('%r is ready', self)
                    break
                except sh.ErrorReturnCode:
                    await asyncio.sleep(0.5)
            else:
                raise TimeoutError('container host failed to become ready')
            
            self.local_socket = local_socket


    async def stop_container_host(self):
        async with self._operation_lock:
            if self.process is not None:
                self.process.terminate()
                self.local_socket = None
                self.process = None

    def out_cb(self, data):
        logger.debug('%r: %s', self, data)

    def process_done(self, *args):
        logger.info('%r: podman terminated', self)
        self.process = None
        self.local_socket = None

    async def podman(self, *args, _log=True,
                     _bg=True, _bg_exc=False, _fg=False):
        await self.start_container_host()
        options = {}
        if _log and self.podman_log:
            options['_out']=str(self.podman_log)
            options['_err_to_out'] = True
        result = sh.podman(
            self.extra_args,
                *args,
                _fg=_fg,
            **options)
        if not _fg:
            await result
        return result

    async def podman_nosocket(self, *args, _log=True, **kwargs):
        options = {}
        await self.start_container_host()
        if _log and self.podman_log:
            options['_out']=str(self.podman_log)
            options['_err_to_out'] = True
        result = self.machine.run_command(
            'podman',
            *args,
            _user=self.user,
            **options,
            **kwargs)
        return await result
    
    @contextlib.asynccontextmanager
    async def filesystem_access(self, *args):
        prefix = []
        if self.user != 'root':
            prefix =['podman', 'unshare']
        res = await self.machine.run_command(
            *prefix,
            'podman',
            *args)
        remote_path_str = str(res.stdout, 'utf-8').strip()
        remote_path_str = os.path.relpath(remote_path_str,'/')
        # Copied and modified from Machine.filesystem_access.
        # Refactoring so there is more shared code did not work out on my first try.
        self.sshfs_count += 1
        try:
            # Argument for correctness of locking.  The goal of
            # sshfs_lock is to make sure that two callers are not both
            # trying to spin up sshfs at the same time.  The lock is
            # never held when sshfs_count is < 1, so it will not block
            # when the coroutine that actually starts sshfs acquires
            # the lock.  Therefore the startup can actually proceed.
            # It would be equally correct to grab the lock before
            # incrementing sshfs_count, but more difficult to
            # implement because the lock must be released by time of
            # yield so other callers can concurrently access the filesystem.
            async with self.sshfs_lock:
                if self.sshfs_count == 1:
                    self.sshfs_path = tempfile.mkdtemp(
                        dir=self.machine.config_layout.state_dir, prefix=self.machine.name, suffix="sshfs_"+self.user)
                    self.sshfs_process = await become_privileged.sshfs_sftp_finder(
                        machine=self.machine,
                        prefix=shlex.join(prefix),
                        sshfs_path=self.sshfs_path,
                        become_privileged_command=self.become_privileged_command
                    )
                    for x in range(5):
                        alive, *rest = self.sshfs_process.process.is_alive()
                        if not alive:
                            await self.sshfs_process
                            raise RuntimeError  # I'd expect that to have happened from an sh exit error already
                        if os.path.exists(os.path.join(
                                self.sshfs_path, remote_path_str)):
                            break
                        await asyncio.sleep(0.4)
                    else:
                        raise TimeoutError("sshfs failed to mount")
            yield Path(self.sshfs_path)/remote_path_str
        finally:
            self.sshfs_count -= 1
            if self.sshfs_count <= 0:
                self.sshfs_count = 0
                try:
                    self.sshfs_process.process.terminate()
                except BaseException:
                    pass
                dir = self.sshfs_path
                self.sshfs_path = None
                self.sshfs_process = None
                await asyncio.sleep(0.2)
                with contextlib.suppress(OSError):
                    if dir:
                        os.rmdir(dir)

    @property
    def become_privileged_command(self):
        user = self.user
        machine = self.machine
        if not hasattr(machine, 'become_privileged_command'):
            return []
        return machine.become_privileged_command(user)

    @property
    def extra_args(self):
        '''Tell Ansible about container_host'''
        return f'--url=unix://{self.local_socket}'

    tar_volume_context = LocalPodmanContainerHost.tar_volume_context
__all__ += ['RemotePodmanHost']

#:InjectionKey to look up a container host.  Can either be a
#:class:`PodmanContainerHost` or a :class:`Machine`.
podman_container_host = InjectionKey('carthage.podman/container_host')


__all__ += ['podman_container_host']

@inject(container_host=InjectionKey(podman_container_host, _optional=True))
async def instantiate_container_host(target, *, container_host):
    '''
    Set *target.container_host* to the appropriate container host.
    '''
    if target.container_host:
        return
    if container_host is None:
        class_name = target.__class__.__module__+'.'+target.__class__.__qualname__
        logger.warning('%s instance %s does not declare a container host; using local podman', class_name, repr(target))
        target.container_host = await target.ainjector(LocalPodmanContainerHost)
        return
    if isinstance(container_host, PodmanContainerHost):
        target.container_host = container_host
        return
    if isinstance(container_host, AbstractMachineModel):
        ainjector = container_host.injector.get_instance(AsyncInjector)
        container_host = await ainjector.get_instance_async(InjectionKey(Machine, _ready=False))
    assert isinstance(container_host, Machine), 'container_host must be a PodmanContainerHost or machine'
    try:
        target.container_host = container_host.injector.get_instance(
            InjectionKey(PodmanContainerHost, host=container_host.name, user=container_host.runas_user))
    except KeyError: # does not exist yet
        from ..local import LocalMachineMixin
        if isinstance(container_host, LocalMachineMixin):
            target.container_host = await target.ainjector(LocalPodmanContainerHost)
        else:
            target.container_host = await container_host.ainjector(RemotePodmanHost, machine=container_host)
        container_host.injector.add_provider(
            InjectionKey(PodmanContainerHost, host=container_host.name, user=container_host.runas_user),
            target.container_host)
    return
