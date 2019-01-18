import os, os.path, pytest
from carthage.dependency_injection import *
import carthage, carthage.ansible
from carthage.pytest import *

state_dir  = os.path.join(os.path.dirname(__file__), "test_state")

@pytest.fixture()
@async_test
@inject(config = carthage.ConfigLayout)
async def configured_ainjector(ainjector, config):
    config.state_dir = state_dir
    ainjector.add_provider(carthage.ssh.SshKey)
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

    
