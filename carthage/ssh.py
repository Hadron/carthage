# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import dataclasses, io, os
from .dependency_injection import inject, AsyncInjector, Injector, AsyncInjectable, Injectable, InjectionKey, dependency_quote
from .config import ConfigLayout
from .setup_tasks import SetupTaskMixin, setup_task
from . import sh, machine
from .utils import memoproperty, when_needed


@dataclasses.dataclass
class RsyncPath:

    machine: machine.Machine
    path: str

    def __repr__(self):
        return f'<Rsync {self.machine}:{self.path}>'

    def __str__(self):
        return f'{self.machine.ip_address}:{self.path}'

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
        
@inject(config_layout = ConfigLayout,
        ainjector = AsyncInjector)
class SshKey(AsyncInjectable, SetupTaskMixin):


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
        no_passphrase = io.StringIO("")
        os.makedirs(self.config_layout.state_dir, exist_ok = True)
        await sh.ssh_keygen(f = self.key_path,
                            _in = no_passphrase,
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

    def rsync(self, *args, ssh_origin = None):
        from .network import access_ssh_origin
        ssh_options = []
        args = list(args)
        for i, a in enumerate(args):
            if isinstance(a, RsyncPath):
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
            return access_ssh_origin(ssh_origin = ssh_origin,
                                     ssh_origin_vrf = vrf)(
                              'rsync',
                              *rsync_opts, *args,
                              _bg = True, _bg_exc = False,
            _env = self.agent.agent_environ)
        else:
            return sh.rsync(*rsync_opts, *args, _bg = True, _bg_exc = False,
            _env = self.agent.agent_environ)
        
        

        return sh.rsync.bake('-e' 'ssh',
                             _env = self.agent.agent_environ)
    
    @memoproperty
    def pubkey_contents(self):
        with open(self.key_path+".pub", "rt") as f:
            return f.read()
        

@inject(config_layout = ConfigLayout,
        ssh_key = SshKey)
class AuthorizedKeysFile(Injectable):

    def __init__(self, config_layout, ssh_key):
        self.path = config_layout.state_dir+'/authorized_keys'
        environ = os.environ.copy()
        environ['PYTHONPATH'] = config_layout.hadron_operations
        sh.python3('-mhadron.inventory.config.default_keys',
                   _env = environ,
                   _out = config_layout.hadron_operations + '/ansible/output/authorized_keys.default')
        with open(config_layout.hadron_operations+"/ansible/output/authorized_keys.default", "rt") as in_keys:
            with open(self.path, "wt") as f:
                f.write(in_keys.read())
                f.write(ssh_key.pubkey_contents)
                

@inject(
    config_layout = ConfigLayout,
    key = SshKey)
class SshAgent(Injectable):

    def __init__(self, config_layout, key):
        state_dir = config_layout.state_dir
        auth_sock = os.path.join(state_dir, "ssh_agent")
        try: os.unlink(auth_sock)
        except FileNotFoundError: pass
        self.process = sh.ssh_agent('-a', auth_sock,
                                    '-D', _bg = True)
        self.auth_sock = auth_sock
        sh.ssh_add(key.key_path, _env = self.agent_environ)
        

    @memoproperty
    def agent_environ(self):
        env = os.environ.copy()
        env['SSH_AUTH_SOCK'] = self.auth_sock
        return env

ssh_agent = when_needed(SshAgent)

__all__ = ('SshKey', 'ssh_agent', 'SshAgent', 'RsyncPath')

