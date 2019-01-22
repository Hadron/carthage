import os, pytest, os.path, shutil
from carthage.dependency_injection import *
import carthage, carthage.ansible
from carthage.pytest import *
from carthage.setup_tasks import *

state_dir  = os.path.join(os.path.dirname(__file__), "test_state")

@pytest.fixture()
def ainjector(ainjector):
    config = ainjector.injector(carthage.ConfigLayout)
    config.state_dir = state_dir
    yield ainjector
    shutil.rmtree(state_dir, ignore_errors = True)

class  Stampable(SetupTaskMixin, AsyncInjectable):

    def __init_subclass__(cls):
        super().__init_subclass__()
        cls.stamp_path = os.path.join(state_dir, str(id(cls)))

@async_test
async def test_basic_setup(ainjector):
    called = 0
    
    class c(Stampable):
        @setup_task("test_stamp")
        def test_stamp_task(self):
            nonlocal called
            called += 1
    assert called == 0
    assert not check_stamp(c.stamp_path, "test_stamp")
    c_obj = await ainjector(c)
    assert called == 1
    assert check_stamp(c.stamp_path, "test_stamp")
    c_obj2 = await ainjector(c)
    assert c_obj is not c_obj2
    assert called == 1
    
@async_test
async def test_check_completed(ainjector):
    called = 0
    is_completed = True
    class c(Stampable):
        @setup_task("check_completed")
        def setup_check_completed(self):
            nonlocal called
            called +=1
        @setup_check_completed.check_completed()
        def setup_check_completed(self):
            return not is_completed
    c_1 = await ainjector(c)
    assert called == 0
    is_completed = False
    c_2 = await ainjector(c)
    assert called == 1
    assert not check_stamp(c.stamp_path, "check_completed")
    
@async_test
async def test_invalidator(ainjector):
    called = 0
    class c(Stampable):
        @setup_task("test_invalidator")
        def setup_invalidator(self):
            nonlocal called
            called += 1
        @setup_invalidator.invalidator()
        def setup_invalidator(self):
            return False
    assert not check_stamp(c.stamp_path, "test_invalidator")
    await ainjector(c)
    assert called == 1
    assert check_stamp(c.stamp_path, "test_invalidator")
    await ainjector(c)
    assert called == 2
    
