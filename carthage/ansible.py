# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import json, os, os.path, tempfile, yaml
from .dependency_injection import *
from . import sh
from .ssh import SshKey
from types import SimpleNamespace


class AnsibleFailure(RuntimeError):

    def __init__(self, msg, ansible_result):
        super().__init__(msg)
        self.ansible_result = ansible_result

    def __str__(self):
        s = super().__str__()
        s += ": "+repr(self.ansible_result)
        return s
    
@inject(injector = Injector,
        ssh_key = SshKey)
async def run_play(hosts, play,
                   raise_on_error = True,
                   gather_facts = False,
                   *, ssh_key, injector):
    with tempfile.TemporaryDirectory() as ansible_dir:
        injector(write_config, ansible_dir, ssh_key = ssh_key)
        with open(os.path.join(ansible_dir,
                               "playbook.yml"), "wt") as f:
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
        res = sh.ansible_playbook("playbook.yml",
i='inventory.txt',
                                  _bg = True, _bg_exc = False,
                                  _cwd = ansible_dir)
        try:
            ansible_exc = None
            await res
        except sh.ErrorReturnCode_2 as e:
            # Remember the exception, preferring it to other
            # exceptions if we get a parse error on the output
            ansible_exc = e
        try:
            json_result = json.loads(res.stdout)
            result = AnsibleResult(json_result)
        except Exception:
            if ansible_exc is not None: raise ansible_exc from None
            raise
        if raise_on_error and not result.success:
            raise AnsibleFailure(f"Ansible Failed running play: {yaml.dump(pb, default_flow_style = False)}", result)
        return result
                                  

@inject(ssh_key = SshKey)
def write_config(ansible_dir,
                 *, ssh_key):
    private_key = None
    if ssh_key:
        private_key = f"private_key_file = {ssh_key.key_path}"
    with open(os.path.join(ansible_dir,
                           "ansible.cfg"), "wt") as f:
        f.write("[defaults]\n")
        f.write(f'''\
stdout_callback = json
retry_files_enabled = false
{private_key}''')
        
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
                    

__all__ = ["localhost_machine", "run_play"]

