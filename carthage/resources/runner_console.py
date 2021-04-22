# Copyright (C) 2019, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage import *
import carthage
from carthage import sh, ssh
config = base_injector(ConfigLayout)
async def async_setup():
    global ssh_key, tmux
    ssh_key = await ainjector.get_instance_async(ssh.SshKey)
    tmux = sh.tmux.bake( 'new-window', _env = ssh_key.agent.agent_environ)

def unbake(cmd):
    "Helper to split out the arguments of a baked sh command"
    return [cmd._path]+list(cmd._partial_baked_args)

async def ssh_to(m: carthage.machine.Machine):
    if in_tmux:
        ssh_key = await ainjector.get_instance_async(carthage.ssh.SshKey)
        sh.tmux( "setenv", "-g", "SSH_AUTH_SOCK", ssh_key.agent.auth_sock)
        sh.tmux( "setenv", "-u", "SSH_AUTH_SOCK")
        tmux(unbake(m.ssh), '-A')
    else:
        await loop.run_in_executor(None, m.ssh('-A', _fg = True))

