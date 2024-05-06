# Copyright (C) 2018, 2019, 2020, 2021, 2022, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import asyncio
import contextlib
import dataclasses
import io
import os
import time
from pathlib import Path
from .dependency_injection import inject, AsyncInjector, Injector, AsyncInjectable, Injectable, InjectionKey, dependency_quote, is_obj_ready
from .config import ConfigLayout
from .setup_tasks import SetupTaskMixin, setup_task
from . import sh, machine
from .utils import memoproperty, when_needed
from pathlib import Path


@dataclasses.dataclass
class RsyncPath:

    '''
    In :func:`rsync`, local paths are represented directly and remote paths are represented as a :class:`RsyncPath`.
    RsyncPath has a target machine (a :class:`~carthage.Machine`) and path.  There is also a *runas_user*.  If not specified *runas_user* defaults to the *runas_user* of the machine.  It is an error to use two RsyncPaths in the same call to rsync with differing *runas_user*.
    '''
    
    machine: machine.Machine
    path: str
    runas_user: str = None

    def __repr__(self):
        return f'<Rsync {self.machine}:{self.path}>'

    def __str__(self):
        return f'{ssh_user_addr(self.machine)}:{self.path}'

    @property
    def relpath(self):
        '''Path relative to the root directory of machine'''
        p = Path(self.path)
        if p.is_absolute():
            return str(p.relative_to("/"))
        else:
            return self.path

    @memoproperty
    def ssh_origin(self):
        from .machine import ssh_origin
        try:
            return self.machine.injector.get_instance(ssh_origin)
        except KeyError:
            return None

    @memoproperty
    def ssh_origin_vrf(self):
        from .machine import ssh_origin_vrf
        return self.machine.injector.get_instance(InjectionKey(ssh_origin_vrf, optional=True))


@inject(
    ainjector=AsyncInjector)
class SshKey(AsyncInjectable, SetupTaskMixin):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config_layout = self.injector(ConfigLayout)

    @memoproperty
    def known_hosts(self):
        return Path(self.config_layout.state_dir)/"ssh_known_hosts"

    async def async_ready(self):
        await self.run_setup_tasks()
        self.agent = await self.ainjector(ssh_agent, key=dependency_quote(self))
        return await super().async_ready()

    @setup_task('gen-key')
    async def generate_key(self):
        os.makedirs(self.config_layout.state_dir, exist_ok=True)
        await sh.ssh_keygen(f=self.key_path,
                            N='',
                            _in=None,
                            _bg=True,
                            _bg_exc=False)

    def add_to_agent(self, agent):
        try:
            sh.ssh_add(self.key_path, _env=agent.agent_environ)
        except sh.ErrorReturnCode:
            time.sleep(2)
            sh.ssh_add(self.key_path, _env=agent.agent_environ)

    @memoproperty
    def key_path(self):
        '''The path of the private key suitable for inclusion with ``ssh -i`` or *None* if no filesystem key exists.
        '''
        return Path(self.config_layout.state_dir)/'ssh_key'

    @memoproperty
    def stamp_path(self):
        return Path(self.config_layout.state_dir)

    @memoproperty
    def ssh(self):
        return sh.ssh.bake(_env=self.agent.agent_environ)

    async def rsync(self, *args, ssh_origin=None):
        return await self.ainjector(
            rsync,
            *args,
            key=dependency_quote(self),
            ssh_origin=ssh_origin)

    @memoproperty
    def pubkey_contents(self):
        with open(str(self.key_path) + ".pub", "rt") as f:
            return f.read()


@inject(
    key=InjectionKey(SshKey, _optional=True),
    config_layout=ConfigLayout,
    injector=Injector)
async def rsync(*args, config_layout,
                injector,
                ssh_origin=None, key=None):
    from .network import access_ssh_origin
    if key:
        ssh_agent = key.agent
    else:
        ssh_agent = injector.get_instance(SshAgent)
    ssh_options = []
    rsync_command = ""
    runas_user = None
    args = list(args)
    async with contextlib.AsyncExitStack() as stack:
        for i, a in enumerate(args):
            if isinstance(a, RsyncPath):
                if runas_user is None:
                    runas_user = a.runas_user
                if runas_user is None:
                    runas_user = a.machine.runas_user
                if a.runas_user is not None and runas_user != a.runas_user:
                    raise RuntimeError('conflicting runas_user between multiple RsyncPaths')
        
                if a.machine.rsync_uses_filesystem_access:
                    path = await stack.enter_async_context(a.machine.filesystem_access(user=runas_user))
                    args[i] = Path(path).joinpath(a.relpath)
                    continue
                if runas_user != a.machine.ssh_login_user:
                    if hasattr(a.machine, 'become_privileged_command'):
                        rsync_command = '--rsync-path='+' '.join(
                            a.machine.become_privileged_command(runas_user)) + " rsync"
                    else:
                        raise RuntimeError('runas_user differs from ssh_login_user but no become privileged mechanism present.')
                sso = a.ssh_origin
                if ssh_origin is None:
                    ssh_origin = sso
                    vrf = a.ssh_origin_vrf
                    ssh_options = list(a.machine.ssh_options) + a.machine.config_layout.global_ssh_options.split()
                elif ssh_origin is not sso:
                    raise RuntimeError(f"Two different ssh_origins: {sso} and {ssh_origin}")
                args[i] = str(a)
        if ssh_options:
            ssh_options = list(ssh_options)

        ssh_options.extend(['-F'+str(ssh_agent.ssh_config)])
        if key:
            ssh_options.extend(['-i'+str(key.key_path)])
        ssh_options = " ".join(ssh_options)
        rsync_opts = ['-e', 'ssh ' + ssh_options]
        if rsync_command:
            rsync_opts .append(rsync_command)


        if ssh_origin:
            return await access_ssh_origin(ssh_origin=ssh_origin,
                                           ssh_origin_vrf=vrf)(
                                               'rsync',
                                               *rsync_opts, *args,
                                               _bg=True, _bg_exc=False,
                                               _env=ssh_agent.agent_environ)
        else:
            return await sh.rsync(*rsync_opts, *args, _bg=True, _bg_exc=False,
                                  _env=ssh_agent.agent_environ)


@inject(
    ssh_key=SshKey,
    injector=Injector)
class AuthorizedKeysFile(Injectable):

    def __init__(self, ssh_key, injector):
        config_layout = injector(ConfigLayout)
        self.path = Path(config_layout.state_dir)/'authorized_keys'
        authorized_keys = config_layout.authorized_keys
        if authorized_keys.startswith('|'):
            authorized_keys = authorized_keys[1:]
            keys_in = str(sh.sh("-c",
                                authorized_keys,
                                _encoding='utf-8'))
        else:
            if not authorized_keys:
                keys_in = ""
            else:
                keys_in = Path(authorized_keys).read_text()
        with open(self.path, "wt") as f:
            f.write(keys_in)
            if ssh_key:
                f.write(ssh_key.pubkey_contents)


@inject(
    injector=Injector,
    key=InjectionKey(SshKey, _optional=True, _ready=False))
class SshAgent(Injectable):

    def __init__(self, injector, key):
        config_layout = injector(ConfigLayout)
        run = Path(config_layout.local_run_dir)
        auth_sock = os.path.join(run, "ssh_agent")
        os.makedirs(run, exist_ok=True)
        try:
            os.unlink(auth_sock)
        except FileNotFoundError:
            pass
        if config_layout.production_ssh_agent and 'SSH_AUTH_SOCK' in os.environ:
            self.auth_sock = os.environ['SSH_AUTH_SOCK']
            self.process = None
        else:
            self.process = sh.ssh_agent('-a', auth_sock,
                                        '-D', _bg=True)
            self.auth_sock = auth_sock
        if key and is_obj_ready(key):
            self.handle_key(key)
        elif key:  # not ready
            future = asyncio.ensure_future(key.async_become_ready())
            future.add_done_callback(lambda f: self.handle_key(f.result()))
        ssh_config = run.joinpath('ssh_config')
        ssh_config_text = f'''
UserKnownHostsFile {config_layout.state_dir}/ssh_known_hosts
        '''
        if os.path.exists('/etc/ssh/ssh_config'):
            ssh_config_text += 'Include /etc/ssh/ssh_config\n'
        if os.path.exists(os.path.expanduser('~/.ssh/config')):
            ssh_config_text += 'Include config\n'
        ssh_config.write_text(ssh_config_text)
        self.ssh_config = ssh_config
        
    def handle_key(self, key):
        key.add_to_agent(self)

    def close(self):
        if self.process is not None:
            try:
                self.process.terminate()
            except BaseException:
                pass
            self.process = None

    @memoproperty
    def agent_environ(self):
        env = os.environ.copy()
        env['SSH_AUTH_SOCK'] = self.auth_sock
        return env


ssh_agent = when_needed(SshAgent)

def ssh_user_addr(machine):
    '''Returns a string like ``root@test.example.com`` from a model
    with *ip_address* of ``test.example.com`` and *ssh_login_user* of
    ``root``.

    :param machine: a :class:`~carthage.machine.Machine` or something else with *ip_address* and *ssh_login_user*.

    This handles things like ssh_login_user being None.

    Note that :meth:`carthage.machine.Machine.ip_address` can best be thought of as the endpoint to connect to for management of a machine.  It is often but not always an ip_address.
    
    '''
    user = machine.ssh_login_user
    connection = machine.ip_address
    if user: return user+'@'+connection
    return connection

@inject(jump_host=InjectionKey('ssh_jump_host', _optional=True))
def ssh_handle_jump_host(jump_host):
    '''
        Returns ssh options for connecting to a jump host.  If *jump_host* is None, returns an empty tuple.

        If *jump_host* is a string, returns that string.

        If jump_host is a Machine or MachineModel, calculates the appropriate jump host specification assuming that machine is to be used.
        '''
    if jump_host is None: return tuple()
    if isinstance(jump_host, str): return ('-oProxyJump='+jump_host,)
    try:
        return ('-oProxyJump='+ssh_user_addr(jump_host),)
    except AttributeError:
        raise AttributeError(f'{jump_host!r} is not a valid jump host')
            
__all__ = ('SshKey', 'ssh_agent', 'SshAgent', 'RsyncPath', 'rsync',
           'ssh_user_addr', 'ssh_handle_jump_host',
           )
