# Copyright (C) 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import contextlib, dataclasses, json, os, os.path, tempfile, typing, yaml
from abc import ABC, abstractmethod
from .dependency_injection import *
from . import sh, Machine, machine
from .container import Container
from .config import ConfigLayout
from .ssh import SshKey
from .utils import validate_shell_safe
from types import SimpleNamespace
from .network import access_ssh_origin
import logging

logger = logging.getLogger("carthage")
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

ansible_origin = InjectionKey("ansible/origin")

__all__ += ['ansible_origin']

class AnsibleConfig:
    '''This is a stub for now.  Long term it should help manage  library path, role path, etc.
'''
    pass
__all__ += ['AnsibleConfig']

@dataclasses.dataclass
class NetworkNamespaceOrigin:
    '''An *origin* to be passed into :func:`.run_playbook` that indicates
    that we wish to run *Ansible* using the network namespace to which
    a :class:`.Container` belongs.  The container could be used
    directly, but in that case *run_playbook* will ssh to the
    container, effectively using both the network and filesystem
    namespace.  In contrast, with this class, the filesystem namespace
    in which *Carthage* runs will be used.
    '''
    namespace: Container

__all__ += ["NetworkNamespaceOrigin"]

@inject(origin = InjectionKey(ansible_origin, optional = True),
        ainjector = AsyncInjector,
        config = ConfigLayout,
        )
async def run_playbook(hosts,
                       playbook: str,
                       inventory: str,
                       log:str = None,
                       *,
                       extra_args = [],
                       raise_on_failure: bool = True,
                       ansible_config = AnsibleConfig(),
                       ainjector,
                       config,
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
    injector = ainjector.injector
    def to_inner(s):
        return config_inner + s[len(config_dir):]
    if not isinstance(hosts, list): hosts = [hosts]
    async with contextlib.AsyncExitStack() as stack:
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
        if origin and not isinstance(origin, NetworkNamespaceOrigin):
            config_dir = await stack.enter_async_context(origin.filesystem_access())
            config_dir += "/ansible"
            os.makedirs(config_dir, exist_ok = True)
            config_inner = "/ansible"
        else:
            config_dir = config.state_dir
            config_inner = config.state_dir
        with await ainjector(
                write_config, config_dir,
                [inventory],
                ansible_config = ansible_config, log = log, origin = origin) as config_file:

            ansible_command += f'ANSIBLE_CONFIG={to_inner(config_file)} ansible-playbook -l"{target_hosts}" {" ".join(extra_args)} {os.path.basename(playbook)}'
            log_args: dict = {}
            if log:
                log_args['_out'] = log
                log_args['_err_to_out'] = True

            if origin and isinstance(origin, NetworkNamespaceOrigin):
                await stack.enter_async_context(origin.namespace.machine_running(ssh_online = True))
                cmd = injector(access_ssh_origin, ssh_origin = origin.namespace)(
"sh",
                    '-c', ansible_command,
                    **log_args,
                    _bg = True,
                    _bg_exc = False)
            elif origin                :
                vrf = origin.injector.get_instance(InjectionKey(machine.ssh_origin_vrf, optional = True))
                if vrf:
                    ansible_command = ansible_command.replace("ansible-playbook",
                                            f'ip vrf exec {vrf} ansible-playbook')
                cmd = origin.ssh( "-A", ansible_command,
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
@contextlib.contextmanager
def write_config(config_dir, inventory,
                 *, ssh_key, log,
                 config, origin,
                 ansible_config):
    private_key = None
    if ssh_key:
        private_key = f"private_key_file = {ssh_key.key_path}"
    if not log:
        stdout_str = "stdout_callback = json"
    else: stdout_str = ""
    with tempfile.TemporaryDirectory(
        dir = config_dir,
        prefix = "ansible") as dir, \
        open(dir+"/ansible.cfg", "wt") as f:
        f.write("[defaults]\n")
        f.write(f'''\
{stdout_str}
retry_files_enabled = false
{private_key}

''')
        if origin is None or isinstance(origin, NetworkNamespaceOrigin):
            os.makedirs(dir+"/inventory/group_vars")
            with open(dir + "/inventory/hosts.ini", "wt") as hosts_file:
                # This may need to be more generalized as Carthage core gains the ability to construct nontrivial Ansible inventory.
                # Another concern is that the way Ansible uses hosts, groups and variables is conceptually similar to how Carthage uses injectors.  It's likely that we want a way to reference more injection keys as dependencies for a play than just  config variables, and to access attributes of those dependencies.
# If those mechanisms become available, we may want to revisit this.
                #This hosts file is empty and only present so the inventory source is valid
                hosts_file.write("[all]\n")
            with open(dir + "/inventory/group_vars/all.yml", "wt") as config_yaml:
                config_yaml.write(yaml.dump(
                    {'config': config.__getstate__()},
                    default_flow_style = False))
            inventory = [dir + "/inventory/hosts.ini"] + inventory
        f.write(f'inventory = {",".join(inventory)}')

        #ssh section
        f.write('''
[ssh_connection]
pipelining=True
''')
        if origin is None or isinstance(origin, NetworkNamespaceOrigin):
            f.write(f'''\
ssh_args = -o ControlMaster=auto -o ControlPersist=60s -oUserKnownHostsFile={config.state_dir}/ssh_known_hosts -oStrictHostKeyChecking=no
''')
        else:
            f.write(f'''\
ssh_args = -o ControlMaster=auto -o ControlPersist=60s -oStrictHostKeyChecking=no
''')
            
    
        f.flush()
        yield dir+"/ansible.cfg"
        



class LocalhostMachine:
    name = "localhost"
    ip_address = "127.0.0.1"

    def ansible_inventory_line(self):
        return "localhost ansible_connection=local"


localhost_machine = LocalhostMachine()

__all__ += ['localhost_machine']

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

__all__ += ['AnsibleResult']

class AnsibleInventory(AsyncInjectable):

    '''A source of ansible inventory.  This class will generate an
        ansible inventory file and optionally transfer it to a target
        :class:`~carthage.machine.Machine`.

        :param destination: A file path or :class:`carthage.ssh.RsyncPath` where the resulting inventory yaml will be placed.


    '''

    def __init__(self, destination, **kwargs):
        self.destination = destination
        super().__init__(**kwargs)

    async def async_ready(self):
        result = await self.generate_inventory()
        await self.write_inventory(self.destination, result)
        await super().async_ready()
        
    async def collect_machines(self):
        self.machines = await self.ainjector.filter_instantiate_async(Machine, ['host'], ready = False)

    async def collect_groups(self):
        plugins = await self.ainjector.filter_instantiate_async(AnsibleGroupPlugin, ['name'], ready = True)
        self.group_plugins = [p[1] for p in plugins]
        group_info: dict[str, dict] = {}
        for k, p in plugins:
            try:
                result = await self.ainjector(p.group_info)
            except:
                logger.exception( f"Error getting group variables from {p.name} plugin:")
                raise
            for group, result_info in result.items():
                group_info.setdefault(group, {})
                for info_type, info_type_dict in result_info:
                    group_info[group].setdefault(info_type, {})
                    #info_type will be vars, hosts or children
                    for k,v in info_type_dict.items():
                        group_info[group][info_type][k] = v
        return group_info

    async def collect_hosts(self, result_dict : dict[str, dict]):
        plugin_filtered = await self.ainjector.filter_instantiate_async(AnsibleHostPlugin, ['name'], ready = True)
        plugins = [p[1] for p in plugin_filtered]
        all = result_dict.setdefault('all', {})
        hosts_dict = all.setdefault('hosts', {})
        for ignore_key, m in self.machines:
            try: machine_name = m.ansible_inventory_name
            except AttributeError: machine_name = m.name
            var_dict: dict[str, dict] = {}
            for p in plugins:
                try:
                    var_dict.update( await self.ainjector(p.host_vars, m))
                except:
                    logger.exception( f"Error getting variables for {machine_name} from {p.name} plugin:")
                    raise
            hosts_dict[machine_name] = var_dict
            groups = []
            for p in self.group_plugins:
                try: groups += await self.ainjector(p.groups_for, m)
                except:
                    logger.exception( f"Error determining groups for {machine_name} from group plugin {p.name}")
                    raise
            for g in groups:
                result_dict.setdefault(g, {})
                result_dict[g].setdefault('hosts', {})
                result_dict[g]['hosts'].setdefault( machine_name, {})

    async def generate_inventory(self):
        await self.collect_machines()
        result = await self.collect_groups()
        await self.collect_hosts(result)
        self.inventory = result
        return result

    async def write_inventory(self, destination, inventory: dict[ str, dict]):
        from . import ssh
        with contextlib.ExitStack() as stack:
            if not isinstance(destination, ssh.RsyncPath):
                local_path = destination
            else:
                dir = stack.enter_context(TemporaryDirectory( dir = self.config_layout.state_dir, prefix = "ansible-inventory"))
                local_path = os.path.join(dir, "hosts.yml")
            with open(local_path, "wt") as f:
                f.write(yaml.dump(inventory, default_flow_style = False))
                if isinstance(destination, ssh.RsyncPath):
                    key = await self.ainjector.get_instance_async(ssh.SshKey)
                    await key.rsync(local_path, destination)
                    
        

        

class AnsibleGroupPlugin(Injectable, ABC):



    @abstractmethod
    async def group_info(self) -> dict[str: dict[str: typing.Any]]:
        '''
        Returns an ansible inventory dictionary.  An example might loolook like the following in yaml::

            group_name:
                vars:
                    var_1: value_1
                children:
                    group_2:
                hosts:
                    host.com:

        '''
        raise NotImplementedError

    @abstractmethod
    async def groups_for(self, m: Machine):
        raise NotImplementedError
    
    @classmethod
    def supplementary_injection_keys(self, k):
        yield InjectionKey(AnsibleGroupPlugin, name=self.name)

        
class AnsibleHostPlugin(AsyncInjectable, ABC):

    @abstractmethod
    async def host_vars(self, m: Machine):
        raise NotImplementedError

    @classmethod
    def supplementary_injection_keys(self, k:InjectionKey):
        yield InjectionKey(AnsibleHostPlugin, name = self.name)

__all__ += ['AnsibleInventory', 'AnsibleGroupPlugin', 'AnsibleHostPlugin']
