# Copyright (C) 2023, 2024, Hadron Industries, Inc.
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

    def __init__(self, name=None, readonly=None, **kwargs):
        super().__init__(**kwargs)
        if name is not None:
            self.name = name
        if readonly is not None:
            self.readonly = readonly

    def __eq__(self, other):
        if not isinstance(other, MockDeployable):
            return NotImplemented
        return other.name == self.name

    def __hash__(self):
        return hash(self.name)
    
    @setup_task("Find or create")
    async def find_or_create(self):
        if res := await self.find():
            return res
        if self.readonly: raise LookupError('readonly true')
        await self.do_create()
        if not await self.find():
            raise LookupError('Object failed to be found after creation')

    @find_or_create.check_completed()
    async def find_or_create(self):
        return await self.find()

    async def find(self):
        return self.name in deployed_deployables

    async def do_create(self):
        deployed_deployables.add(self.name)

    async def delete(self):
        deployed_deployables.remove(self.name)

    async def dynamic_dependencies(self):
        return []

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

    async def find_orphans(self, deployables):
        expected_deployables = set(d.name for d in deployables if isinstance(d,MockDeployable))
        results = []
        for n in deployed_deployables - expected_deployables:
            results.append(await self.ainjector(MockDeployable, name=n, readonly=True))
        return results
    
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
    orphans = await ainjector(find_orphan_deployables)
    assert len(orphans) == 0
    

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
    print(result_full_run)

@async_test
async def test_deploy_destroy(ainjector):
    class layout(CarthageLayout):

        class delete_me_second(MockDeployable):

            name = 'delete_me_second'

            async def delete(self):
                dependency = await self.ainjector.get_instance_async('simple_delete')
                if await dependency.find():
                    raise RuntimeError('You must delete simple_delete before delete_me_second')
                await super().delete()

            async def dynamic_dependencies(self):
                return [InjectionKey('delete_me_third')]

        @inject_autokwargs(
            dependency=InjectionKey('delete_me_second'))
        class simple_delete(MockDeployable):

            name = 'simple_delete'

            async def dynamic_dependencies(self):
                return [InjectionKey('delete_me_third')]

        class  delete_me_third(MockDeployable):

            name = 'delete_me_third'

        class dependency_failure(MockDeployable):

            name = 'dependency_failure'


        @inject_autokwargs(
            dependency=InjectionKey('dependency_failure'))
        class failing_delete(MockDeployable):

            name = 'failing_delete'

            async def delete(self):
                raise RuntimeError('I have a bug in my delete function')


        class readonly_not_deleted(MockDeployable):

            name = 'readonly_not_deleted'

            readonly = True

        class retain_not_deleted(MockDeployable):

            name = 'retain_not_deleted'

            add_provider(destroy_policy, DeletionPolicy.retain)

    ainjector.add_provider(layout)
    l = await ainjector.get_instance_async(layout)
    ainjector = l.ainjector
    # We force create the readonly object
    await l.readonly_not_deleted.do_create()
    result_deploy = await ainjector(run_deployment)
    assert result_deploy.is_successful()
    result_dry_run = await ainjector(run_deployment_destroy, dry_run=True)
    assert l.readonly_not_deleted in result_dry_run.ignored
    assert l.retain_not_deleted in result_dry_run.ignored
    assert l.simple_delete in result_dry_run.successes
    result = await ainjector(run_deployment_destroy)
    print(result)
    successes = result.successes
    assert l.simple_delete in successes
    assert l.delete_me_second in successes
    assert l.delete_me_third in successes
    assert len(successes) == 3
    ignored = result.ignored
    assert l.readonly_not_deleted in ignored
    assert l.retain_not_deleted in ignored
    assert len(ignored) == 2
    assert l.failing_delete in result.failures
    assert len(result.failures) == 1
    assert l.dependency_failure in result.dependency_failures
    assert len(result.dependency_failures) == 1
    for d in successes:
        assert not await d.find()
    for d in await ainjector(find_deployables):
        if d in successes: continue
        assert await d.find()
        
@async_test
async def test_find_orphans(ainjector):
    class layout(CarthageLayout):

        class has_dynamic_dependency(MockDeployable):

            name = 'has_dynamic_dependency'

            async def async_ready(self):
                # Deploy a deployable called dynamic
                await self._dynamic()
                return await super().async_ready()

            async def _dynamic(self):
                '''
                Create a dynamic deployable.
                If called from async_ready, will actually bring the deployable to ready and thus will deploy it.
                If called from dynamic_dependencies, will be in an instantiate_not_ready context, so will not deploy.
                '''
                return await self.ainjector(MockDeployable, name='dynamic_dependency')

            async def dynamic_dependencies(self):
                return [await self._dynamic()]

        class normal(MockDeployable):

            name = 'normal'

    ainjector.add_provider(layout)
    l = await ainjector.get_instance_async(layout)
    lainjector = l.ainjector
    result = await lainjector(run_deployment)
    assert l.has_dynamic_dependency in result.successes
    assert l.normal in result.successes
    with instantiation_not_ready():
        dynamic = await l.has_dynamic_dependency._dynamic()
    assert await dynamic.find()
    orphan = await lainjector(MockDeployable, name='orphan')
    # If we search too far up the hierarchy, everything should be an orphan
    orphans = await ainjector(find_orphan_deployables)
    assert len(orphans) == 4
    assert l.normal in orphans
    assert orphan in orphans
    # But only the orphan should be an orphan if we include the layout's context
    orphans = await lainjector(find_orphan_deployables)
    assert len(orphans) == 1
    assert orphan in orphans
    
