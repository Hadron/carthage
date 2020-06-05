# Copyright (C) 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import json, os, os.path, tempfile, yaml
from .dependency_injection import *
from . import sh, Machine
from .config import ConfigLayout
from .ssh import SshKey
from .utils import validate_shell_safe
from types import SimpleNamespace
__all__ = []

class AnsibleFailure(RuntimeError):

    def __init__(self, msg, ansible_result = None):
        super().__init__(msg)
        self.ansible_result = ansible_result

    def __str__(self):
        s = super().__str__()
        if self.ansible_result:
            s += ": "+repr(self.ansible_result)
        return s

__all__ += ['AnsibleFailure']

ansible_origin = InjectionKey(Machine, role="ansible/origin")

__all__ += ['ansible_origin']

class AnsibleConfig:
    '''This is a stub for now.  Long term it should help manage  library path, role path, etc.
'''
    pass
__all__ += ['AnsibleConfig']

@inject(origin = InjectionKey(ansible_origin, optional = True),
        ainjector = AsyncInjector,
        )
async def run_playbook(hosts,
                       playbook: str,
                       inventory: str,
                       log:str = None,
                       *,
                       raise_on_failure: bool = True,
                       ansible_config,
                       ainjector,
                       origin = None,
                       ):
    '''
    Run an ansible playbook against a set of hosts.  If *origin* is ``None``, then ``ansible-playbook`` is run locally, otherwise it is run via ``ssh`` to ``origin``.
    A new ansible configuration is created for each run of ansible.
    if *log* is ``None`` then ansible output is parsed and an :class:`AnsibleResult` is returned.  Otherwise ``True`` is returned on a successful ansible run.

    :param playbook: Path to the playbook local to *origin*.  The current directory when ansible is run will be the directory containing the playbook.

    :param hosts: A list of hosts to run the play against.  Hosts can be strings, or :class:`Machine` objects.  For machine objects, *ansible_inventory_name* will be tried before :meth:`Machine.name`.

:param log: A local path where a log of output and errors should be created or ``None`` to parse the ansible result programatically after ansible completes.  It is not possible to produce human readable output and a result that can be parsed at the same time without writing a custom ansible callback plugin.

    '''
    target_hosts = []
    for h in hosts:
        if isinstance(h, str):
            target_hosts.append(h)
        else:
            if hasattr(h, 'ansible_inventory_name'):
                target_hosts.append(h.ansible_inventory_name)
            else: target_hosts.append(h.name)
    list(map(lambda h: validate_shell_safe(h), target_hosts))
    target_hosts = ":".join(target_hosts)
    playbook_dir = os.path.dirname(playbook)
    validate_shell_safe(playbook)
    if playbook_dir:
        ansible_command = f'cd "{playbook_dir}"&& '
    else: ansible_command = ''
    assert origin is None
    config_file = await ainjector(write_config, ansible_config = ansible_config, log = log)

    ansible_command += f'ANSIBLE_CONFIG={config_file.name} ansible-playbook -l"{target_hosts}" {os.path.basename(playbook)}'
    log_args = {}
    if log:
        log_args['_out'] = log
        log_args['_err_to_out'] = True
    with config_file:
        if origin:
            cmd = origin.ssh(ansible_command,
                         _bg = True,
                         _bg_exc = False,
                         **log_args)
        else:
            cmd = sh.sh(
            '-c', ansible_command,
            **log_args,
            _bg = True,
            _bg_exc = False)
        try:
            ansible_exc = None
            await cmd
        except (sh.ErrorReturnCode_2, sh.ErrorReturnCode_4) as e:
            # Remember the exception, preferring it to other
            # exceptions if we get a parse error on the output
            ansible_exc = e
        except Exception as e:
            if log: log_str = f'; logs in {log}'
            else: log_str = ""
            raise AnsibleFailure(f'Failed Running {playbook} on {target_hosts}{log_str}') from e
    if log and ansible_exc:
        raise AnsibleFailure(f'Failed running {playbook} on {target_hosts}; logs in {log}') from ansible_exc
    elif log: return True
    try:
        json_out = json.loads(cmd.stdout)
        result = AnsibleResult(json_out)
    except  Exception:
        if ansible_exc: raise ansible_exc from None
        raise
    if (not result.success ) and raise_on_failure:
        raise AnsibleFailure(f'Failed running {playbook} on{target_hosts}', result)
    return result

__all__ += ['run_playbook']


@inject(ainjector = AsyncInjector,
        ssh_key = SshKey)
async def run_play(hosts, play,
                   raise_on_failure = True,
                   gather_facts = False,
                   *,
                   log = None,
                   ssh_key, ainjector):
    '''
    Run a single Ansible play, specified as a python dictionary.
    The typical usage of this function is for cases where code wants to use an Ansible module to access some resource, especially when the results need to be programatically examined.  As an example, this can be used to examine the output of Ansible modules that gather facts.

    **If you are considering loading a YAML file, parsing it, and calling this function, you are almost certainly better served by :func:`run_playbook`.**
'''
    with tempfile.TemporaryDirectory() as ansible_dir:
        config = AnsibleConfig()
        with open(os.path.join(ansible_dir,
                               "playbook.yml"), "wt") as f:
            if isinstance(play, dict): play = [play]
            pb = [{
                'hosts': ":".join([h.name for h in hosts]),
                'remote_user': 'root',
                'gather_facts': gather_facts,
                'tasks': play}]
            f.write(yaml.dump(pb, default_flow_style = False))
        with open(os.path.join(ansible_dir,
                               "inventory.txt"), "wt") as f:

            f.write("[hosts]\n")
            for h in hosts:
                try:
                    f.write(h.ansible_inventory_line()+"\n")
                except AttributeError:
                    f.write(f'{h.name} ansible_ip={h.ip_address}\n')
        return await ainjector(run_playbook,
                               hosts,
                               ansible_dir+"/playbook.yml",
                               ansible_dir+"/inventory.txt",
                               ansible_config = config,
                               origin = None,
                               raise_on_failure = raise_on_failure,
                               log = log)

__all__ += ['run_play']


@inject(ssh_key = SshKey,
        config = ConfigLayout)
def write_config(
                 *, ssh_key, log,
        config, ansible_config):
    private_key = None
    if ssh_key:
        private_key = f"private_key_file = {ssh_key.key_path}"
    if not log:
        stdout_str = "stdout_callback = json"
    else: stdout_str = ""
    f =tempfile.NamedTemporaryFile(
        dir = config.state_dir,
        encoding = 'utf-8',
        mode = "wt",
        prefix = "ansible", suffix = ".cfg")
    f.write("[defaults]\n")
    f.write(f'''\
{stdout_str}
retry_files_enabled = false
{private_key}

        [ssh_connection]
pipelining=True
''')
    f.flush()
    return f

class LocalhostMachine:
    name = "localhost"
    ip_address = "127.0.0.1"

    def ansible_inventory_line(self):
        return "localhost ansible_connection=local"


localhost_machine = LocalhostMachine()

class AnsibleResult:

    def __init__(self, res):
        self.json = res
        self.failures = 0
        self.unreachable = 0
        self.changed = 0
        self.ok = 0
        for v in res['stats'].values():
            self.ok += v['ok']
            self.failures += v['failures']
            self.changed += v['changed']
            self.unreachable += v['unreachable']
        self.parse_plays(res)
        self.host_stats = res['stats']



    def parse_plays(self, res):
        self.tasks = {}
        for p in res['plays']:
            for t in p['tasks']:
                t.update(t['task'])
                del t['task']
                t['duration'] = SimpleNamespace(**t['duration'])
                if t['name'] in self.tasks:
                    raise ValueError(f"{t['name']} duplicated")
                if len(t['hosts']) == 1:
                    for v in t['hosts'].values():
                        t.update(v)
                self.tasks[t['name']] = SimpleNamespace(**t)

    @property
    def success(self):
        return (self.unreachable == 0) and (self.failures == 0)

    def __repr__(self):
        res = f"<AnsibleResult: \
failures: {self.failures}; unreachable: {self.unreachable}; ok: {self.ok}; changed: {self.changed};\
plays:[{[k for k in self.tasks.keys()]}]"

        if self.failures:
            for t in self.tasks.values():
                try:
                    if t.failed:
                        res += f"\n\tFatal {t.name}: {t.msg}"
                except AttributeError: pass
        res +=">"
        return res
