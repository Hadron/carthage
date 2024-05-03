# Copyright (C) 2019, 2020, 2021, 2022, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import contextlib
import dataclasses
import json
import os
import os.path
import tempfile
import typing
import yaml
import importlib.resources
from abc import ABC, abstractmethod
from pathlib import Path

from .dependency_injection import *
from . import sh, Machine, machine
from .container import Container
from .config import ConfigLayout
from .ssh import SshKey, SshAgent
from .utils import validate_shell_safe
from types import SimpleNamespace
from .network import access_ssh_origin
from . import setup_tasks
from .plugins import CarthagePlugin
import logging

logger = logging.getLogger("carthage")
__all__ = []


class AnsibleFailure(RuntimeError):

    def __init__(self, msg, ansible_result=None):
        super().__init__(msg)
        self.ansible_result = ansible_result

    def __str__(self):
        s = super().__str__()
        if self.ansible_result:
            s += ": " + repr(self.ansible_result)
        return s


__all__ += ['AnsibleFailure']

ansible_origin = InjectionKey("ansible/origin")
ansible_log = InjectionKey("ansible/log")

__all__ += ['ansible_origin', "ansible_log"]


@inject(injector=Injector)
class AnsibleConfig(Injectable):
    '''
    Capture carthage plugins providing ansible resources etc.
    '''

    def __init__(self, injector):
        # If this class is ever modified to store the injector, then it should be
        # passed into the superclass so it can be claimed.
        super().__init__()
        roles = []
        filters = []
        for k, pl in injector.filter_instantiate(CarthagePlugin, ['name']):
            roles_path = pl.resource_dir / "ansible/roles"
            if roles_path.exists():
                roles += [str(roles_path)]
            filter_path = pl.resource_dir / "ansible/filter_plugins"
            if filter_path.exists():
                filters.append(str(filter_path))
        self.roles = roles
        self.filter_plugins = filters


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
        self.machines = await self.ainjector.filter_instantiate_async(Machine, ['host'], ready=False)

    async def collect_groups(self):
        plugins = await self.ainjector.filter_instantiate_async(AnsibleGroupPlugin, ['name'], ready=True)
        plugins = sorted(plugins, key=lambda x: getattr(x[0], 'priority', 100), reverse=True)
        self.group_plugins = [p[1] for p in plugins]
        group_info: dict[str, dict] = {}
        for k, p in plugins:
            try:
                result = await self.ainjector(p.group_info)
            except BaseException:
                logger.exception(f"Error getting group variables from {p.name} plugin:")
                raise
            for group, result_info in result.items():
                group_info.setdefault(group, {})
                for info_type, info_type_dict in result_info.items():
                    group_info[group].setdefault(info_type, {})
                    # info_type will be vars, hosts or children
                    for k, v in info_type_dict.items():
                        group_info[group][info_type][k] = v
        return group_info

    async def collect_hosts(self, result_dict: dict[str, dict]):
        plugin_filtered = await self.ainjector.filter_instantiate_async(AnsibleHostPlugin, ['name'], ready=True)
        plugin_filtered = sorted(plugin_filtered, key=lambda x: getattr(x[0], 'priority', 100), reverse=True)
        plugins = [p[1] for p in plugin_filtered]
        all = result_dict.setdefault('all', {})
        hosts_dict = all.setdefault('hosts', {})
        for ignore_key, m in self.machines:
            try:
                machine_name = m.ansible_inventory_name
            except AttributeError:
                machine_name = m.name
            var_dict: dict[str, dict] = {}
            for p in plugins:
                try:
                    var_dict.update(await self.ainjector(p.host_vars, m))
                except BaseException:
                    logger.exception(f"Error getting variables for {machine_name} from {p.name} plugin:")
                    raise
            if 'ansible_host' not in var_dict:
                try:
                    var_dict['ansible_host'] = m.ip_address
                except Exception:
                    pass
            if 'ansible_ssh_common_args' not in var_dict:
                try:
                    if ssh_options := m.ssh_options:
                        var_dict['ansible_ssh_common_args'] = " ".join(ssh_options)
                except Exception:
                    pass
            if 'ansible_user' not in var_dict:
                try:
                    if m.ssh_login_user != 'root':
                        var_dict['ansible_user'] = m.ssh_login_user
                except Exception:
                    pass
            hosts_dict[machine_name] = var_dict
            groups = []
            for p in self.group_plugins:
                try:
                    groups += await self.ainjector(p.groups_for, m)
                except BaseException:
                    logger.exception(f"Error determining groups for {machine_name} from group plugin {p.name}")
                    raise
            for g in groups:
                result_dict.setdefault(g, {})
                result_dict[g].setdefault('hosts', {})
                result_dict[g]['hosts'].setdefault(machine_name, {})

    async def generate_inventory(self):
        await self.collect_machines()
        result = await self.collect_groups()
        await self.collect_hosts(result)
        self.inventory = result
        return result

    async def write_inventory(self, destination, inventory: dict[str, dict]):
        from . import ssh
        with contextlib.ExitStack() as stack:
            if not isinstance(destination, ssh.RsyncPath):
                local_path = Path(destination)
                os.makedirs(local_path.parent, exist_ok=True)
            else:
                dir = stack.enter_context(
                    TemporaryDirectory(
                        dir=self.config_layout.state_dir,
                        prefix="ansible-inventory"))
                local_path = os.path.join(dir, "hosts.yml")
            with open(local_path, "wt") as f:
                f.write(yaml.dump(inventory, default_flow_style=False))
                if isinstance(destination, ssh.RsyncPath):
                    key = await self.ainjector.get_instance_async(ssh.SshKey)
                    await key.rsync(local_path, destination)


class AnsibleGroupPlugin(Injectable, ABC):

    @abstractmethod
    async def group_info(self) -> dict[str: dict[str: typing.Any]]:
        '''
        Returns an ansible inventory dictionary.  An example might look like the following in yaml::

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
    def default_class_injection_key(self):
        return InjectionKey(AnsibleGroupPlugin, name=self.name)


class AnsibleHostPlugin(AsyncInjectable, ABC):

    @abstractmethod
    async def host_vars(self, m: Machine):
        raise NotImplementedError

    @classmethod
    def default_class_injection_key(self):
        return InjectionKey(AnsibleHostPlugin, name=self.name)


__all__ += ['AnsibleInventory', 'AnsibleGroupPlugin', 'AnsibleHostPlugin']


@inject(origin=InjectionKey(ansible_origin, optional=True),
        inventory=AnsibleInventory,
        log=InjectionKey(ansible_log, optional=True),
        ainjector=AsyncInjector,
        ansible_config=AnsibleConfig,
        config=ConfigLayout,
        )
async def run_playbook(hosts,
                       playbook: str,
                       inventory: str,
                       log: str = None,
                       *,
                       extra_args=[],
                       raise_on_failure: bool = True,
                       ansible_config,
                       ainjector,
                       config,
                       origin=None,
                       ):

    '''
    Run an ansible playbook against a set of hosts.
    A new ansible configuration is created for each run of ansible.
    if *log* is ``None`` then ansible output is parsed and an :class:`AnsibleResult` is returned.  Otherwise ``True`` is returned on a successful ansible run.

    :param playbook: Path to the playbook local to *origin*.  The current directory when ansible is run will be the directory containing the playbook.

    :param hosts: A list of hosts to run the play against.  Hosts can be strings, or :class:`Machine` objects.  For machine objects, *ansible_inventory_name* will be tried before :meth:`Machine.name`.  If a machine is not running and has :meth:`ansible_not_running_context`, then that asyncronous context manager will be entered.  The return from that context manager will be used as inventory variables for the machine.  For example :class:`~carthage.container.Container` uses this to arrange to run ansible plays without booting a container.

:param log: A local path where a log of output and errors should be created or ``None`` to parse the ansible result programatically after ansible completes.  It is not possible to produce human readable output and a result that can be parsed at the same time without writing a custom ansible callback plugin.

:param origin: Controls where the playbook is run:

    None
        Run locally

    A :class:`~carthage.machine.Machine`
        If the origin is a :class:`Container` that is not running, run ansible-playbook directly in the namespace of the container.  Otherwise ssh into the machine and run ansible-playbook.

    NetworkNamespaceOrigin
        Use the local filesystem but the network namespace of the referenced container.

    '''
    injector = ainjector.injector

    def to_inner(s):
        s = str(s)
        return str(config_inner) + s[len(str(config_dir)):]
    if not isinstance(hosts, list):
        hosts = [hosts]
    async with contextlib.AsyncExitStack() as stack:
        target_hosts = []
        inventory_overrides = {}
        for h in hosts:
            if isinstance(h, str):
                target_hosts.append(h)
            else:
                if hasattr(h, 'ansible_inventory_name'):
                    target_name = h.ansible_inventory_name
                else:
                    target_name = h.name
                if not h.running and hasattr(h, 'ansible_not_running_context'):
                    inventory_overrides[target_name] = await stack.enter_async_context(
                        h.ansible_not_running_context())
                elif hasattr(h, 'ansible_inventory_overrides'):
                    inventory_overrides[target_name] = h.ansible_inventory_overrides
                target_hosts.append(target_name)

        list(map(lambda h: validate_shell_safe(h), target_hosts))
        target_hosts = ":".join(target_hosts)
        playbook = str(playbook)
        playbook_dir = os.path.dirname(playbook)
        validate_shell_safe(playbook)
        if isinstance(inventory, AnsibleInventory):
            inventory = inventory.destination
        if playbook_dir:
            ansible_command = f'cd "{playbook_dir}"&& '
        else:
            ansible_command = ''
        if origin and not isinstance(origin, NetworkNamespaceOrigin):
            config_dir = await stack.enter_async_context(origin.filesystem_access())
            config_dir = config_dir / "ansible"
            os.makedirs(config_dir, exist_ok=True)
            config_inner = "/ansible"
        else:
            config_dir = config.state_dir
            config_inner = config.state_dir
        if isinstance(origin, Container) and not origin.running:
            # This may not be strictly true, but we definitely
            # cannot get access to the json
            log = "container.log"
        with await ainjector(
                write_config, config_dir,
                [inventory],
                ansible_config=ansible_config, log=log,
                inventory_overrides=inventory_overrides,
                origin=origin) as config_file:

            ansible_command += f'ANSIBLE_CONFIG={to_inner(config_file)} ansible-playbook -l"{target_hosts}" {" ".join(extra_args)} {os.path.basename(playbook)}'
            log_args: dict = {}
            if log:
                log_args['_out'] = log
                log_args['_err_to_out'] = True

            if origin and isinstance(origin, NetworkNamespaceOrigin):
                await stack.enter_async_context(origin.namespace.machine_running(ssh_online=True))
                cmd = injector(access_ssh_origin, ssh_origin=origin.namespace)(
                    "sh",
                    '-c', ansible_command,
                    **log_args,
                    _bg=True,
                    _bg_exc=False)
            elif origin:
                vrf = origin.injector.get_instance(InjectionKey(machine.ssh_origin_vrf, optional=True))
                if vrf:
                    ansible_command = ansible_command.replace("ansible-playbook",
                                                              f'ip vrf exec {vrf} ansible-playbook')
                if isinstance(origin, Container) and not origin.running:
                    cmd = origin.container_command("sh", "-c", ansible_command)
                else:
                    cmd = origin.ssh("-A", ansible_command,
                                     _bg=True,
                                     _bg_exc=False,
                                     **log_args)
            else:
                cmd = sh.sh(
                    '-c', ansible_command,
                    **log_args,
                    _bg=True,
                    _bg_exc=False)
            try:
                ansible_exc = None
                await cmd
            except (sh.ErrorReturnCode_2, sh.ErrorReturnCode_4) as e:
                # Remember the exception, preferring it to other
                # exceptions if we get a parse error on the output
                ansible_exc = e
            except Exception as e:
                if log:
                    log_str = f'; logs in {log}'
                else:
                    log_str = ""
                raise AnsibleFailure(f'Failed Running {playbook} on {target_hosts}{log_str}') from e
        if log and ansible_exc:
            raise AnsibleFailure(f'Failed running {playbook} on {target_hosts}; logs in {log}') from ansible_exc
        elif log:
            return True
        try:
            json_out = json.loads(cmd.stdout)
            result = AnsibleResult(json_out)
        except Exception:
            if ansible_exc:
                raise ansible_exc from None
            raise
        if (not result.success) and raise_on_failure:
            raise AnsibleFailure(f'Failed running {playbook} on{target_hosts}', result)
        return result

__all__ += ['run_playbook']


@inject(ainjector=AsyncInjector,
        inventory=InjectionKey(AnsibleInventory, _optional=True),
        origin=InjectionKey(ansible_origin, _optional=True),
        log=InjectionKey(ansible_log, _optional=True),
        )
async def run_play(hosts, play,
                   *, raise_on_failure=True,
                   gather_facts=False,
                   base_vars=None,
                   vars=None, inventory=None,
                   log=None,
                   origin=None,
                   ainjector):
    '''
    Run a single Ansible play, specified as a python dictionary.
    The typical usage of this function is for cases where code wants to use an Ansible module to access some resource, especially when the results need to be programatically examined.  As an example, this can be used to examine the output of Ansible modules that gather facts.

    **If you are considering loading a YAML file, parsing it, and calling this function, you are almost certainly better served by :func:`run_playbook`.**
    :param vars: Run through :func:`resolve_deferred` to produce a dictionary of variables.  Can be a function, an InjectionKey, or a dict.

    :param base_vars: A dictionary of variables to set.  Unlike *vars* this must be a dictionary mapping strings to variable values or None.  *vars* is typically left to the user writing a Carthage layout, where as *base_vars* is used by higher-level constructs like :func:`ansible_role_task` to set variables related to privilege escalation from a customization or Machine.  Variables in *vars* override variables in *base_vars*.
    
'''
    if base_vars is None:
        base_vars = {}
    async with contextlib.AsyncExitStack() as stack:
        if origin and not isinstance(origin, NetworkNamespaceOrigin):
            root_path = await stack.enter_async_context(origin.filesystem_access())
            ansible_dir = stack.enter_context(tempfile.TemporaryDirectory(dir=root_path, prefix="ansible-"))
        else:
            ansible_dir = stack.enter_context(tempfile.TemporaryDirectory())
            root_path = "/"
        ansible_dir = Path(ansible_dir)
        with open(os.path.join(ansible_dir,
                               "playbook.yml"), "wt") as f:
            if isinstance(play, dict):
                play = [play]
            pb = [{
                'hosts': ":".join([h.name for h in hosts]),
                'remote_user': 'root',
                'gather_facts': gather_facts,
                'tasks': play}]
            vars = await resolve_deferred(ainjector, item=vars, args={})
            if vars:
                base_vars.update(vars)
            if base_vars:
                pb[0]['vars'] = base_vars
            f.write(yaml.dump(pb, default_flow_style=False))
        if inventory is None:
            with open(os.path.join(ansible_dir,
                                   "inventory.txt"), "wt") as f:
                f.write("[hosts]\n")
                for h in hosts:
                    if not h.running and hasattr(h, 'ansible_not_running_context'):
                        continue
                    try:
                        f.write(h.ansible_inventory_line() + "\n")
                    except AttributeError:
                        try:
                            f.write(f'{h.name} ansible_ip={h.ip_address}\n')
                        except (NotImplementedError, AttributeError):
                            pass
            inventory = f'/{ansible_dir.relative_to(root_path)}/inventory.txt'
        return await ainjector(run_playbook,
                               hosts,
                               f'/{ansible_dir.relative_to(root_path)}/playbook.yml',
                               inventory=inventory,
                               raise_on_failure=raise_on_failure,
                               log=log,
                               origin=dependency_quote(origin))

__all__ += ['run_play']


@inject(ssh_key=InjectionKey(SshKey, _optional=True),
        ssh_agent=InjectionKey(SshAgent, _optional=True),
        config=ConfigLayout)
@contextlib.contextmanager
def write_config(config_dir, inventory,
                 *, ssh_key, log,
                 config, origin,
                 inventory_overrides,
                 ssh_agent,
                 ansible_config):
    private_key = ""
    if ssh_key:
        ssh_agent = ssh_key.agent
        private_key = f"private_key_file = {ssh_key.key_path}"
    if not log:
        stdout_str = "stdout_callback = json"
    else:
        stdout_str = ""
    with tempfile.TemporaryDirectory(
            dir=config_dir,
            prefix="ansible") as dir, \
            open(dir + "/ansible.cfg", "wt") as f:
        f.write("[defaults]\n")
        f.write(f'''\
{stdout_str}
retry_files_enabled = false
roles_path={":".join(ansible_config.roles)}
filter_plugins={":".join(ansible_config.filter_plugins)}
{private_key}

''')
        if inventory_overrides or origin is None or isinstance(origin, NetworkNamespaceOrigin):
            os.makedirs(dir + "/inventory/group_vars")
            with open(dir + "/inventory/hosts.yml", "wt") as hosts_file:
                # This may need to be more generalized as Carthage
                # core gains the ability to construct nontrivial
                # Ansible inventory.  Another concern is that the way
                # Ansible uses hosts, groups and variables is
                # conceptually similar to how Carthage uses injectors.
                # It's likely that we want a way to reference more
                # injection keys as dependencies for a play than just
                # config variables, and to access attributes of those
                # dependencies.  If those mechanisms become available,
                # we may want to revisit this.  This hosts file is
                # sometimes empty if there are no inventory_overrides,
                # but is still needed to make a valid inventory
                # source.
                hosts_file.write(yaml.dump(
                    dict(all=dict(hosts=inventory_overrides)),
                    default_flow_style=False))

            with open(dir + "/inventory/group_vars/all.yml", "wt") as config_yaml:
                config_yaml.write(yaml.dump(
                    {'config': config.__getstate__()},
                    default_flow_style=False))
            inventory = [dir + "/inventory/hosts.yml"] + inventory
        f.write(f'inventory = {",".join(inventory)}')

        # ssh section
        f.write('''
[ssh_connection]
pipelining=True
''')
        if origin is None or isinstance(origin, NetworkNamespaceOrigin):
            f.write(f'''\
ssh_args = -F{ssh_agent.ssh_config} -o ControlMaster=auto -o ControlPersist=60s -oUserKnownHostsFile={config.state_dir}/ssh_known_hosts {config.global_ssh_options}
''')
        else:
            f.write(f'''\
ssh_args = -o ControlMaster=auto -o ControlPersist=60s -oStrictHostKeyChecking=no
''')

        f.flush()
        yield dir + "/ansible.cfg"


class LocalhostMachine:
    name = "localhost"
    ip_address = "127.0.0.1"
    running = True

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
                        t.update({k:v[k] for k in v if k != 'name'})
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
                except AttributeError:
                    pass
        res += ">"
        return res


__all__ += ['AnsibleResult']


def _handle_host_origin(host, origin):
    base_vars = {}
    runas_user = getattr(host, 'runas_user', None)
    if not isinstance(host, Machine) and hasattr(host, 'host'):
        host = host.host
        if runas_user is None:
            runas_user = host.runas_user
    if runas_user != host.ssh_login_user:
        base_vars['ansible_become_user'] = runas_user
        base_vars['ansible_become'] = True
    if not origin:
        return host, {}, base_vars
    if isinstance(host, Container) and not host.running:
        return localhost_machine, dict(origin=dependency_quote(host)), base_vars
    return host, dict(origin=dependency_quote(host)), base_vars


class ansible_playbook_task(setup_tasks.TaskWrapper):

    extra_attributes = frozenset({'dir', 'playbook'})

    def __init__(self, playbook, origin=False, **kwargs):
        async def func(inst):
            host, extra_args, base_vars = _handle_host_origin(inst, origin)
            args = []
            if base_vars:
                for k,v in base_vars.items():
                    args.append(shlex.join(f'-e{k}={v}'))
                    
            return await inst.ainjector(run_playbook, host, self.dir.joinpath(self.playbook),  extra_args=args,
                                        **extra_args)
        super().__init__(
            func=func,
            description=f'Run {playbook} playbook',
            **kwargs)
        self.playbook = playbook
        self.dir = None

    def __set_name__(self, owner, name):
        import sys
        module = sys.modules[owner.__module__]
        try:
            self.dir = importlib.resources.files(module.__package__)
        except (AttributeError, ValueError):
            self.dir = Path(module.__file__).parent


__all__ += ['ansible_playbook_task']


def ansible_role_task(roles, vars=None,
                      before=None, order=None, origin=False):
    '''
    A :func:`setup_task` to apply one or more ansible roles to a machine.

    :param roles: A single role (as a string) or list of roles to include.  Roles can also be a list of dictionaries containing arguments to *import_role*.

    :param vars: An optional dictionary of ansible variable assignments.
    :param origin: If True, use host as origin from which to run ansible.

    '''
    @setup_tasks.setup_task(f'Apply {roles} ansible roles',
                            before=before, order=order)
    @inject(ainjector=AsyncInjector)
    async def apply_roles(self, ainjector):
        host, extra_args, base_vars = _handle_host_origin(self, origin)
        play = []
        for r in roles:
            if isinstance(r, dict):
                r_dict = r
            else:
                r_dict = dict(name=r)
            play.append(dict(
                import_role=r_dict))

        return await ainjector(
            run_play,
            hosts=[host],
            play=play,
            vars=vars,
            base_vars=base_vars,
            **extra_args
        )
    if isinstance(roles, (str, dict)):
        roles = [roles]
    return apply_roles


__all__ += ['ansible_role_task']

@inject(model=machine.AbstractMachineModel)
def ansible_log_for_model(model):
    '''
    used like::
        add_provider(ansible_log, ansible_log_for_model, allow_multiple=True)

    Sets up a per-model ansible log in *stamp_path*/ansible.log.
    '''
    return f'{model.stamp_path}/ansible.log'

__all__ += ['ansible_log_for_model']

class AnsibleIpAddressMixin(Machine):

    '''Normally, ansible inventory is generated at generation time,
    which may be before cloud services have determined the IP address
    of machines.  This Mixin will look at ip_address at runtime to set

    In a     :class:`carthage.modeling.MachineModel`, use this as follows::

        class server(MachineModel):
            machine_mixins = (AnsibleIpAddressMixin,)


        '''

    @property
    def ansible_inventory_overrides(self):
        return {
            'ansible_host':self.ip_address,
            }

__all__ += ['AnsibleIpAddressMixin']
