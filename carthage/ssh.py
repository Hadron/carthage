# Copyright (C) 2018, 2019, 2020, 2021, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import contextlib, dataclasses, io, os, time
from pathlib import Path
from .dependency_injection import inject, AsyncInjector, Injector, AsyncInjectable, Injectable, InjectionKey, dependency_quote
from .config import ConfigLayout
from .setup_tasks import SetupTaskMixin, setup_task
from . import sh, machine
from .utils import memoproperty, when_needed
from pathlib import Path


@dataclasses.dataclass
class RsyncPath:

    machine: machine.Machine
    path: str

    def __repr__(self):
        return f'<Rsync {self.machine}:{self.path}>'

    def __str__(self):
        return f'{self.machine.ip_address}:{self.path}'

    @property
    def relpath(self):
        '''Path relative to the root directory of machine'''
        p = Path(self.path)
        if p.is_absolute():
            return str(p.relative_to("/"))
        else: return self.path
        
    @memoproperty
    def ssh_origin(self):
        from .machine import ssh_origin
        try:
            return self.machine.injector.get_instance(ssh_origin)
        except KeyError: return None

    @memoproperty
    def ssh_origin_vrf(self):
        from .machine import ssh_origin_vrf
        return self.machine.injector.get_instance(InjectionKey(ssh_origin_vrf, optional = True))
        
@inject(
        ainjector = AsyncInjector)
class SshKey(AsyncInjectable, SetupTaskMixin):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config_layout = self.injector(ConfigLayout)
        

    @memoproperty
    def known_hosts(self):
        return self.config_layout.state_dir+"/ssh_known_hosts"
    
        
    async def async_ready(self):
        await self.run_setup_tasks()
        self.agent = await self.ainjector(ssh_agent, key = dependency_quote(self))
        del self.ainjector
        return await super().async_ready()

    @setup_task('gen-key')
    async def generate_key(self):
        os.makedirs(self.config_layout.state_dir, exist_ok = True)
        await sh.ssh_keygen(f = self.key_path,
                            N = '',
                            _in = None,
                            _bg = True,
                            _bg_exc = False)


    @memoproperty
    def key_path(self):
        return self.config_layout.state_dir+'/ssh_key'

    @memoproperty
    def stamp_path(self):
        return self.config_layout.state_dir

    @memoproperty
    def ssh(self):
        return sh.ssh.bake(_env = self.agent.agent_environ)

    async def rsync(self, *args, ssh_origin = None):
        from .network import access_ssh_origin
        ssh_options = []
        args = list(args)
        async with contextlib.AsyncExitStack() as stack:
            for i, a in enumerate(args):
                if isinstance(a, RsyncPath):
                    if a.machine.rsync_uses_filesystem_access:
                        path = await stack.enter_async_context(a.machine.filesystem_access())
                        args[i] = Path(path).joinpath(a.relpath)
                        continue
                    sso = a.ssh_origin
                    if ssh_origin is None:
                        ssh_origin = sso
                        vrf = a.ssh_origin_vrf
                        ssh_options = a.machine.ssh_options
                    elif ssh_origin is not sso:
                        raise RuntimeError(f"Two different ssh_origins: {sso} and {ssh_origin}")
                    args[i] = str(a)
            if ssh_options:
                ssh_options = list(ssh_options)

        
            ssh_options.extend(['-oUserKnownHostsFile='+self.known_hosts])
            ssh_options = " ".join(ssh_options)
            rsync_opts = ('-e', 'ssh '+ssh_options)
        
            if ssh_origin:
                return await access_ssh_origin(ssh_origin = ssh_origin,
                                     ssh_origin_vrf = vrf)(
                                         'rsync',
                                         *rsync_opts, *args,
                                         _bg = True, _bg_exc = False,
                                         _env = self.agent.agent_environ)
            else:
                return await sh.rsync(*rsync_opts, *args, _bg = True, _bg_exc = False,
            _env = self.agent.agent_environ)

    
    @memoproperty
    def pubkey_contents(self):
        with open(self.key_path+".pub", "rt") as f:
            return f.read()
        

@inject(
        ssh_key = SshKey,
    injector=Injector)
class AuthorizedKeysFile(Injectable):

    def __init__(self,  ssh_key, injector):
        config_layout = injector(ConfigLayout)
        self.path = config_layout.state_dir+'/authorized_keys'
        authorized_keys = config_layout.authorized_keys
        if authorized_keys.startswith('|'):
            authorized_keys = authorized_keys[1:]
            keys_in = str(sh.sh( "-c",
                         authorized_keys,
                         _encoding = 'utf-8'))
        else:
            if not authorized_keys: keys_in = ""
            else:
                keys_in = Path(authorized_keys).read_text()
        with open(self.path, "wt") as f:
            f.write(keys_in)
            f.write(ssh_key.pubkey_contents)
                

@inject(
    injector=Injector,
    key = SshKey)
class SshAgent(Injectable):

    def __init__(self, injector, key):
        config_layout = injector(ConfigLayout)
        run = config_layout.local_run_dir
        auth_sock = os.path.join(run, "ssh_agent")
        os.makedirs(run, exist_ok=True)
        try: os.unlink(auth_sock)
        except FileNotFoundError: pass
        if config_layout.production_ssh_agent and 'SSH_AUTH_SOCK' in os.environ:
            self.auth_sock = os.environ['SSH_AUTH_SOCK']
            self.process = None
        else:
            self.process = sh.ssh_agent('-a', auth_sock,
                                    '-D', _bg = True)
            self.auth_sock = auth_sock
        try:
            sh.ssh_add(key.key_path, _env = self.agent_environ)
        except sh.ErrorReturnCode:
            time.sleep(2)
            sh.ssh_add(key.key_path, _env = self.agent_environ)
            
    def close(self):
        if self.process is not None:
            try: self.process.terminate()
            except: pass
            self.process = None

    @memoproperty
    def agent_environ(self):
        env = os.environ.copy()
        env['SSH_AUTH_SOCK'] = self.auth_sock
        return env

ssh_agent = when_needed(SshAgent)

__all__ = ('SshKey', 'ssh_agent', 'SshAgent', 'RsyncPath')

