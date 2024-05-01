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
import uuid
import dateutil.parser
import carthage.machine
from carthage.dependency_injection import *
from .. import sh, ConfigLayout, become_privileged
from ..machine import AbstractMachineModel, Machine
from ..utils import memoproperty
from ..oci import *

__all__ = []

logger = logging.getLogger('carthage.podman')



class PodmanContainerHost(AsyncInjectable):

    @memoproperty
    def podman_log(self):
        return self.injector.get_instance(InjectionKey("podman_log", _optional=True))
    
    def podman(self, *args,
               _bg=True, _bg_exc=True):
        raise NotImplementedError

    async def filesystem_access(self, container):
        raise NotImplementedError

    async def tar_volume_context(self, volume):
        '''
        An asynchronous context manager that tars up a volume and provides a path to that tar file usable in ``podman import``.  Typical usage::

            async with container_host.tar_volume_context(container_image) as path:
                await container_host.podman('import', path)

        On local systems this manages temporary directories.  For remote container hosts, this manages to get the tar file to the remote system and clean up later.
        '''
        raise NotImplementedError

    async def start_container_host(self):
        pass

    @property
    def extra_args(self):
        '''Extra arguments to pass to podman from ansible plugin.
        '''
        return ''
    
class LocalPodmanContainerHost(PodmanContainerHost):

        
    
    @contextlib.asynccontextmanager
    async def filesystem_access(self, container):
        result = await self.podman(
            'container', 'mount',
            container,
            _bg=True, _bg_exc=False, _log=False)
        try:
            path = str(result).strip()
            yield Path(path)
        finally:
            pass  # Perhaps we should unmount, but we'd need a refcount to do that.

    def podman(self, *args,
               _bg=True, _bg_exc=False, _log=True):
        options = {}
        if _log and self.podman_log:
            options['_out']=str(self.podman_log)
            options['_err_to_out'] = True
        return sh.podman(
            *args,
            _bg=_bg, _bg_exc=_bg_exc,
            _encoding='utf-8',
            **options)

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
            return f'<PodmanContainerHost on {self.machine.name}'
        except Exception:
            return '<PodmanContainerHost>'

    async def start_container_host(self):
        machine = self.machine
        await machine.start_machine()
        if self.local_socket:
            return
        async with self._operation_lock:
            become_privileged_command = []
            if hasattr(machine, 'become_privileged_command'):
                become_privileged_command = machine.become_privileged_command(self.user)
            #xxx we should probe for home directory
            if self.user == 'root':
                home_directory = '/root'
            else:
                home_directory = '/home/'+self.user
            socket_directory = home_directory+'/.carthage/podman_sockets'
            socket = socket_directory+'/'+str(uuid.uuid4())
            await machine.run_command(
            'mkdir', '-p', socket_directory,
            _user=self.user)
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
                _bg=True, _bg_exc=False,
                _done=self.process_done)
            for i in range(5):
                try:
                    await sh.podman('info')
                    break
                except sh.ErrorReturnCode:
                    await asyncio.sleep(0.5)
                    
            self.local_socket = local_socket
        

    async def stop_container_host(self):
        async with self._operation_lock:
            if self.process is not None:
                self.process.terminate()
                self.local_socket = None
                self.process = None

    def process_done(self, *args):
        self.process = None
        self.local_socket = None
        
    def podman(self, *args, _log=True,
               _bg=True, _bg_exc=False):
        options = {}
        if _log and self.podman_log:
            options['_out']=str(self.podman_log)
            options['_err_to_out'] = True
            #breakpoint()
        return sh.podman(
            self.extra_args,
                *args,
                **options)

    @contextlib.asynccontextmanager
    async def filesystem_access(self, container):
        prefix = []
        if self.user != 'root':
            prefix ='podman unshare'
        res = await self.machine.run_command(
            *prefix.split(' '),
            'podman',
            'container',
            'mount',
            container)
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
                        prefix=prefix,
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
async def find_container_host(target, *, container_host):
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
        container_host = await ainjector.get_instance_async(Machine)
        
    assert isinstance(container_host, Machine), 'container_host must be a PodmanContainerHost or machine'
    target.container_host = await ainjector(RemotePodmanHost, machine=container_host)
    return
    
