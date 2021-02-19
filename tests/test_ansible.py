import os, os.path, pytest
import machine_mock
from carthage.dependency_injection import *
import carthage, carthage.ansible
from carthage.pytest import *

from carthage.modeling import *

state_dir  = os.path.join(os.path.dirname(__file__), "test_state")

@pytest.fixture()
@async_test
@inject(config = carthage.ConfigLayout)
async def configured_ainjector(ainjector, config):
    config.state_dir = state_dir
    ainjector.add_provider(carthage.ssh.SshKey)
    enable_modeling_ansible(ainjector)
    return ainjector

@async_test
async def test_run_play(configured_ainjector):
    ainjector = configured_ainjector
    res = await ainjector(carthage.ansible.run_play, [carthage.ansible.localhost_machine],
                                          [{"debug": "msg=foo"}])

@async_test
async def test_run_failing_play(configured_ainjector):
    ainjector = configured_ainjector
    with pytest.raises(carthage.ansible.AnsibleFailure):
        res = await ainjector(carthage.ansible.run_play, [carthage.ansible.localhost_machine],
                                          [{"fail": "msg=foo"}])

    
@async_test
async def test_ansible_with_log(configured_ainjector):
    ainjector = configured_ainjector
    try: os.unlink(state_dir+"/ansible.log")
    except: pass
    await ainjector(carthage.ansible.run_play,
                    [carthage.ansible.localhost_machine],
                    {'debug': 'msg=barbaz'},
                    log = state_dir+"/ansible.log")
    with open(state_dir+"/ansible.log", "rb") as f:
        log_contents = f.read()
    assert b'barbaz' in log_contents
                    

@async_test
async def test_inventory(configured_ainjector):
    ainjector = configured_ainjector
    class Layout(ModelGroup):

        add_provider(machine_implementation_key, dependency_quote(machine_mock.Machine))
        domain = "example.com"

        class m1(MachineModel):

            ansible_vars = dict(foo = 90)
            ansible_groups = ['bar', 'baz']

    layout = await ainjector(Layout)
    ainjector = layout.injector.get_instance(AsyncInjector)
    inventory = await ainjector(carthage.ansible.AnsibleInventory, os.path.join(state_dir, "inventory.yml"))
    for g in layout.m1.ansible_groups:
        assert 'm1.example.com' in inventory.inventory[g]['hosts']
        assert inventory.inventory['all']['hosts']['m1.example.com']['foo'] == 90
        
    
