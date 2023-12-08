# Copyright (C) 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import pytest

from carthage import *
from carthage.modeling import *
from carthage.deployment import *
from carthage.pytest import *

class MockDeployable(InjectableModel, SetupTaskMixin, AsyncInjectable):

    name = None

    readonly=False
    @setup_task("Find or create")
    async def find_or_create(self):
        if res := await self.find():
            return res
        await self.do_create()
        if not await self.find():
            raise LookupError('Object failed to be found after creation')

    @find_or_create.check_completed()
    async def find_or_create(self):
        return await self.find()

    async def find(self):
        return self in deployed_deployables

    async def do_create(self):
        deployed_deployables.add(self)

    async def delete(self):
        deployed_deployables.remove(self)

    def __str__(self):
        return 'Deployable:'+self.name

    def __repr__(self):
        return f'{self.__class__.__name__}(name={self.name})'

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if cls.name:
            propagate_key(InjectionKey(MockDeployable, deployable_name=cls.name), cls)


class MockDeployableFinder(DeployableFinder):
    name = 'mock'

    async def find(self, ainjector):
        result = await ainjector.filter_instantiate_async(
            MockDeployable, lambda k:True,
            stop_at=ainjector,
            ready=False)
        return [x[1] for x in result]

deployed_deployables = set()

@pytest.fixture()
def ainjector(ainjector):
    deployed_deployables.clear()
    ainjector.add_provider(MockDeployableFinder)
    return ainjector

    
    
@async_test
async def test_run_deployment_simple(ainjector):
    class layout(CarthageLayout):

        @inject(ci=InjectionKey("continuous_integration"))
        class good_software(MockDeployable):
            name = 'good software'

        class devops(Enclave):
            domain = 'devops'

            @propagate_key(InjectionKey('continuous_integration', _globally_unique=True))
            class continuous_integration(MockDeployable):

                name ='continuous_integration'

    ainjector.add_provider(layout)
    l = await ainjector.get_instance_async(CarthageLayout)
    ainjector = l.ainjector
    deployables = await ainjector(find_deployables)
    assert len(deployables) == 2
    result = await ainjector(run_deployment)
    assert l.good_software in result.successes
    assert l.devops.continuous_integration in result.successes
    assert len(result.successes)==2
    assert result.is_successful()

@async_test
async def test_failure_detection(ainjector):
    class layout(CarthageLayout):

        @inject(not_buggy=InjectionKey("software_without_bugs"))
        class good_software(MockDeployable):
            name = 'good software'

        @propagate_key(InjectionKey("software_without_bugs", _globally_unique=True))
        class software_without_bugs(MockDeployable):

            name ='software without bugs'

            async def do_create(self):
                raise NotImplementedError("We're not quite there yet.")
                

    ainjector.add_provider(layout)
    l = await ainjector.get_instance_async(CarthageLayout)
    ainjector = l.ainjector
    result = await ainjector(run_deployment, dry_run=True)
    assert len(result.successes) == 2
    assert l.software_without_bugs in result.successes # At least for dry run
    result_full_run = await ainjector(
        run_deployment, deployables=result)
    assert l.software_without_bugs in [x.deployable for x in result_full_run.failures]
    assert l.good_software in [x.deployable for x in result_full_run.dependency_failures]
