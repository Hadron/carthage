# Copyright (C)  2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import asyncio
import json
import logging
import dateutil.parser
from carthage.dependency_injection import *
from . import sh
from .machine import AbstractMachineModel, Machine
from .utils import memoproperty
from .oci import *


logger = logging.getLogger('carthage.podman')

def podman_port_option(p: OciExposedPort):
    return f'-p{p.host_ip}:{p.host_port}:{p.container_port}'

__all__ = []

class PodmanContainerHost:

    def podman(self, *args,
               _bg=True, _bg_exc=True):
        raise NotImplementedError

class LocalPodmanContainerHost(PodmanContainerHost):

    def podman(self, *args,
               _bg=True, _bg_exc=True):
        return sh.podman(
            *args,
            _bg=_bg, _bg_exc=_bg_exc,
            _encoding='utf-8')
    
    
@inject_autokwargs(
    oci_container_image=InjectionKey(oci_container_image, _optional=NotPresent)
    )
class PodmanContainer(Machine, OciContainer):

    '''
An OCI container implemented using ``podman``.  While it is possible to set up a container to be accessible via ssh and to meet all the interfaces of :class:`~carthage.machine.SshMixin`, this is relatively uncommon.  Such containers often have an entry point that is not a full init, and only run one service or program.  Typically :meth:`container_exec` is used to execute an additional command in the scope of a container rather than using :meth:`ssh`.  
    '''

    #: Timeout in seconds to wait when stopping a container
    stop_timeout = 10

    @property
    def ssh_options(self):
        if not hasattr(self, 'ssh_port'):
            raise ValueError('Set ssh_port before ssh')
        return (
            '-oStrictHostKeyChecking=no',
            f'-l{self.ssh_user}',
            f'-p{self.ssh_port}')

    ssh_user = 'root'

    #:The port on which to connect to for ssh
    ssh_port: int
    
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._operation_lock = asyncio.Lock()

    @memoproperty
    def podman(self):
        return self.container_host.podman

    @memoproperty
    def container_host(self):
        return LocalPodmanContainerHost()
    
    
    async def find(self):
        try:
            result = await self.podman(
                'container', 'inspect', self.full_name,
                _bg=True, _bg_exc=False)
        except sh.ErrorReturnCode:
            return False
        containers = json.loads(str(result))
        self.container_info = containers[0]
        ports = self.container_info['NetworkSettings']['Ports']
        if not hasattr(self, 'ssh_port') and '22/tcp' in ports:
            self.ssh_port = ports['22/tcp'][0]['HostPort']
        return dateutil.parser.isoparse(containers[0]['Created']).timestamp()

    async def do_create(self):
        await self.podman(
            'container', 'create',
            f'--name={self.full_name}',
            *self._podman_create_options(),
            self.oci_container_image,
            _bg=True, _bg_exc=False)

    def _podman_create_options(self):
        options = []
        if self.oci_interactive: options.append('-i')
        if self.oci_tty: options.append('-t')
        for p in self.exposed_ports:
            options.append(podman_port_option(p))
        return options
        
    async def delete(self, force=True, volumes=True):
        force_args = []
        if force: force_args.append('--force')
        if volumes: force_args.append('--volumes')
        await self.podman(
            'container', 'rm',
            *force_args, self.full_name,
            _bg=True, _bg_exc=False)

    async def is_machine_running(self):
        assert await self.find()
        self.running =  self.container_info['State']['Running']
        return self.running

    async def start_machine(self):
        async with self._operation_lock:
            await self.is_machine_running()
            if self.running: return
            await self.start_dependencies()
            await super().start_machine()
            logger.info(f'Starting {self.name}')
            await self.podman(
                'container', 'start', self.full_name,
                _bg=True, _bg_exc=False)
        await self.is_machine_running()
    async def stop_machine(self):
        async with self._operation_lock:
            if not self.running:
                return
            await self.podman(
                'container', 'stop',
                f'-t{self.stop_timeout}',
                self.full_name,
                _bg=True, _bg_exc=False)
            self.running = False
            await super().stop_machine()

    def container_exec(self, *args):
        '''
'Execute a command in a running container and return stdout.  This function intentionally has a differentname than :meth:`carthage.container.Container.container_command` because that method does not expect the container to be running.
'''
        result =  self.podman(
            'container', 'exec',
            self.full_name,
            *args,
            )
        return result

    #: An alias to be more compatible with :class:`carthage.container.Container`
    shell = container_exec

    def __repr__(self):
        try: host = repr(self.container_host)
        except Exception: host = "repr failed"
        return f'<{self.__class__.__name__ {self.name} on {host}>'
    
__all__ += ['PodmanContainer']
