# Copyright (C) 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.
import dataclasses
import os
import shutil
import pytest
from pathlib import Path
import carthage
from carthage.pytest import *
from carthage import *
from carthage.kvstore import *
from carthage.modeling import *

class TestAssignments(HashedRangeAssignments):

    __test__ = False

    def __init__(self, objs, **kwargs):
        super().__init__(domain="test_domain", **kwargs)
        self.objs = objs

    def hash_key(self, key, obj):
        if hasattr(obj, 'hash'):
            assert obj.low <= obj.hash <= obj.high
            return obj.low, obj.hash, obj.high
        return super().hash_key(key, obj)

    def do_assignments(self):
        self.new_assignments()
        for o in self.objs:
            self._assign(o.key, o)

    def record_assignment(self, key, obj, assignment):
        obj.assignment = int(assignment)

    def valid_key(self, key):
        for o in self.objs:
            if o.key == key: return True
        return False

    def find_bounds(self, obj):
        return obj.low, obj.high

    def check_consistency(self):
        assigned = set()
        for o in self.objs:
            assert o.assignment is not None
            assert o.assignment not in assigned
            assigned.add(o.assignment)
            assert o.low <= o.assignment <= o.high
            

key_counter = 0
def next_key():
    global key_counter
    key_counter +=1
    return f'k{key_counter}'


@dataclasses.dataclass
class AssignedObj:

    low: int
    high: int
    assignment: int = None
    key: str = dataclasses.field(default_factory=next_key)

state_dir = Path(__file__).parent.joinpath("test_state")


@pytest.fixture()
def ainjector(ainjector):
    ainjector = ainjector.claim("test_kvstore.py")
    config = ainjector.injector(carthage.ConfigLayout)
    config.state_dir = state_dir
    os.makedirs(state_dir, exist_ok=True)
    ainjector.add_provider(KvStore)
    yield ainjector
    shutil.rmtree(state_dir, ignore_errors=True)

@async_test
async def test_do_assign(ainjector):
    o1 = AssignedObj(0,4)
    o2 = AssignedObj(2,3)
    objs = [o1, o2]
    assignments = await ainjector(TestAssignments, objs)
    assignments.do_assignments()
    assignments.check_consistency()


@async_test
async def test_exhaustion(ainjector):
    o1 = AssignedObj(0,1)
    o2 = AssignedObj(0,1)
    o3 = AssignedObj(0,1)
    objs = [o1, o2, o3]
    assignments = await ainjector(TestAssignments, objs)
    with pytest.raises(AssignmentsExhausted): assignments.do_assignments()

@async_test
async def test_reallocate_when_needed(ainjector):
    o1 = AssignedObj(0,1)
    o1.hash = 0
    o2 = AssignedObj(0,1)
    o2.hash = 1
    o3 = AssignedObj(0,1)
    objs = [o1,o2]
    assignments = await ainjector(TestAssignments, objs)
    assignments.do_assignments()
    assert o1.assignment == 0
    assert o2.assignment == 1
    assignments.check_consistency()
    assignments.objs = [o2,o3]
    with pytest.raises(AssignmentsExhausted):
        # exhausted because without confirming all keys are known, we
        # don't know that o1 has been removed
        assignments.do_assignments()
    assignments.enable_key_validation()
    assignments.do_assignments()
    assignments.check_consistency()
    
@async_test
async def test_reallocation_minimized(ainjector):
    "If an object is removed for a temporary time it should not lose its assignment if resources are available"
    o1 = AssignedObj(1,4)
    o2 = AssignedObj(1,4)
    o3 = AssignedObj(1,4)
    o1.hash = 1
    o2.hash = 1
    o3.hash = 1
    #Everyone wants the same assignment
    objs = [o1,o2]
    assignments = await ainjector(TestAssignments, objs)
    assignments.enable_key_validation()
    assignments.do_assignments()
    assignments.check_consistency()
    assert o1.assignment == 1
    assignments.objs = [o3, o2]
    assignments.do_assignments()
    # and should still be consistent with o1 added
    assignments.objs.append(o1)
    assignments.check_consistency()
    # But we get different behavior if we prefer reallocation
    assignments.prefer_reallocate = True
    o4 = AssignedObj(1,4)
    o4.hash = 1
    assignments.objs = [o4,o2, o3]
    assignments.do_assignments()
    assert o4.assignment == 1
    assignments.check_consistency()

@async_test
async def test_dump_load(ainjector):
    o1 = AssignedObj(1,5)
    o2 = AssignedObj(2,6)
    o3 = AssignedObj(1,6)
    o4 = AssignedObj(3,5)
    objs = [o1, o2, o3, o4]
    assignments = await ainjector(TestAssignments, objs)
    assignments.do_assignments()
    kvstore = ainjector.get_instance(KvStore)
    kvstore.dump(state_dir/'dump.yml', lambda d, k,v: True)
    correct_assignments = {o.key:o.assignment for o in objs}
    with kvstore.environment.begin(write=True) as txn, txn.cursor() as csr:
        csr.first()
        while csr.delete(): pass
    kvstore.load(state_dir/'dump.yml')
    assignments2 = await ainjector(TestAssignments, objs)
    for o in objs: o.hash = 1 # so that without db do_assignments produces different results.
    assignments2.do_assignments()
    for o in objs:
        assert o.assignment == correct_assignments[o.key]
        
    
class layout(CarthageLayout):
    class config(NetworkConfigModel):
        add('eth0', mac=None, net=injector_access('pool_network'),
            )

    @provides('pool_network')
    class pool_network(NetworkModel):
        v4_config = V4Config(
            network='192.168.1.0/24',
            dhcp=True,
            pool=(
                '192.168.1.10', '192.168.1.90'),
        )

    class a(MachineModel): pass
    class b(MachineModel): pass

@async_test
async def test_network_pool(ainjector):
    ainjector.add_provider(layout)
    l = await ainjector.get_instance_async(layout)
    kvstore = ainjector.get_instance(KvStore)
    l.pool_network.assign_addresses()
    v4_pool = await l.pool_network.ainjector.get_instance_async(V4Pool)
    for l in l.pool_network.network_links:
        assert v4_pool.valid_key(v4_pool.link_key(l))
        
    
