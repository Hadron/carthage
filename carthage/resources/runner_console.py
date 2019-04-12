from carthage import *
from carthage import sh
config = base_injector(ConfigLayout)
from carthage.hadron_layout import database_key
db = base_injector.get_instance(database_key)
ssh_key = base_injector.get_instance(ssh.SshKey)
tmux = sh.tmux.bake( 'new-window', _env = ssh_key.agent.agent_environ)

def unbake(cmd):
    "Helper to split out the arguments of a baked sh command"
    return [cmd._path]+list(cmd._partial_baked_args)

async def ssh_to(m: carthage.machine.Machine):
    if in_tmux:
        ssh_key = await ainjector.get_instance_async(carthage.ssh.SshKey)
        sh.tmux( "setenv", "-g", "SSH_AUTH_SOCK", ssh_key.agent.auth_sock)
        tmux(unbake(m.ssh), '-A')
    else:
        await loop.run_in_executor(None, m.ssh('-A', _fg = True))

