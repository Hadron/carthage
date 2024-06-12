# Copyright (C) 2021, 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import pytest
import asyncio
from pathlib import Path
import shutil
from carthage.pytest import *
from carthage.dependency_injection import *
from carthage import base_injector, Machine, setup_task, ConfigLayout, LocalMachine
from carthage.modeling.base import *
from carthage.modeling.implementation import ModelingContainer
from carthage.modeling.decorators import *
from carthage.network import NetworkConfig, Network
from carthage.machine import MachineCustomization, BaseCustomization
import carthage.machine


@pytest.fixture()
def injector():
    injector = base_injector(Injector)
    injector.claim()
    config = injector.get_instance(ConfigLayout)
    base_dir = Path(__file__).parent
    base_dir /= "state"
    config.base_dir = str(base_dir)
    yield injector
    injector.close()
    try:
        shutil.rmtree(base_dir)
    except OSError: pass

@pytest.fixture()
def ainjector(injector):
    return injector(AsyncInjector)


def test_modeling_class_injection(injector):
    class Layout(InjectableModel):
        class nc(NetworkConfig):
            pass
    model = injector(Layout)
    nc = model.injector.get_instance(NetworkConfig)
    assert nc is Layout.nc


def test_namespace_cascade(injector):
    class Layout(InjectableModel):
        green = "green"

        class inner(InjectableModel):
            assert green == "green"
            green = "blue"

        class inner2 (InjectableModel):
            inner = inner

            class MoreInner:
                # This should break the chain because it is not a InjectableModel
                class MostInner(InjectableModel):
                    assert 'inner' not in locals().keys()

    res = injector(Layout.inner)
    assert res.green == "blue"
    assert Layout.inner2.inner is Layout.inner


def test_container(injector):
    class Foo(Injectable):
        pass

    def extra_container(domain):
        d = domain

        @provides(InjectionKey(ModelGroup, constraint=20))
        class Baz(ModelGroup):
            domain = d

            class server(MachineModel):
                name = "server"
        return Baz

    class Layout(InjectableModel, metaclass=ModelingContainer):

        add_provider(InjectionKey("key1"),
                     42)
        access_key_1 = injector_access("key1")

        net_config = injector_access(InjectionKey(NetworkConfig, domain="evil.com"))

        class RedEnclave(Enclave):

            domain = "evil.com"

            @propagate_key(InjectionKey(NetworkConfig))
            class nc(NetworkConfig):
                pass

            include_container(extra_container)

            @provides("site-network", InjectionKey(Network, name="red"))
            class SiteNetwork(NetworkModel):
                add_provider(InjectionKey(Foo), Foo, propagate=True)
                name = "red"

    res = injector(Layout)
    nc = res.injector.get_instance(InjectionKey(
        NetworkConfig, domain="evil.com", _ready=False))
    assert nc is Layout.RedEnclave.nc
    machine = res.injector.get_instance(InjectionKey(MachineModel, host="server.evil.com", _ready=False))
    assert isinstance(machine, MachineModel)
    assert res.net_config is res.RedEnclave.nc
    assert res.access_key_1 == 42
    foo = res.injector.get_instance(InjectionKey(
        Foo, domain="evil.com", name="red",
        _ready=False))
    assert isinstance(foo, Foo)


def test_dynamic_name(injector):
    class Layout(ModelGroup):

        for i in range(3):
            @dynamic_name(f'i{i+1}')
            class ignored(InjectableModel):
                square = i * i
    assert Layout.i3.square == 4


@async_test
async def test_machine_model(injector):
    class Layout(ModelGroup):

        class Red(Enclave):

            domain = "evil.com"

            class NetConfig (NetworkConfigModel):
                pass

            class Router(MachineModel):
                pass

            class task_machine(MachineModel):

                @setup_task("Frob the frobbables")
                def frob_stuff(self): pass

        red_router = injector_access(InjectionKey(MachineModel, host="router.evil.com"))

    ainjector = injector(AsyncInjector)
    res = await ainjector(Layout)
    assert res.red_router.name == "router.evil.com"
    assert hasattr(res.Red.task_machine, 'setup_tasks')
    await res.Red.task_machine.async_become_ready()


@pytest.mark.xfail(reason="ResolvableModel makes more things async")
@async_test
async def test_example_model(ainjector):
    from carthage.modeling.example import Layout
    res = await ainjector(Layout)
    nc = res.net_config
    assert isinstance(nc, NetworkConfigModel)
    samba = res.injector.get_instance(InjectionKey(MachineModel, host="samba.evil.com"))
    assert samba.network_config is nc


@async_test
async def test_transclusion(ainjector):
    injector = ainjector.injector
    class Moo(carthage.machine.ResolvableModel): pass
    moo = Moo()
    injector.add_provider(InjectionKey(MachineModel, host="moo.com"),
                          moo)
    injector.add_provider(InjectionKey(Machine, host="mar.com"),
                          "baz")

    class FirstLayout(ModelGroup):

        @model_mixin_for(host="mar.com")
        class MarMixin(MachineModel):
            mixed_in = True

    first_layout = await ainjector(FirstLayout)
    injector = first_layout.injector

    class Layout(ModelGroup):

        @transclude_overrides()
        class moo(MachineModel):
            name = "moo.com"

        class mar(*injector(model_bases, "mar.com", MachineModel)):
            name = "mar.com"

    ainjector = injector(AsyncInjector)
    l = await ainjector(Layout)
    assert l.moo is moo
    assert l.mar.machine == "baz"
    assert l.mar.mixed_in == True


@async_test
async def test_generate_and_network(ainjector):

    class Layout(ModelGroup):

        class net(NetworkModel):

            name = "the-net"

        class nc(NetworkConfigModel):
            other_machine = injector_access("other-machine")
            net = injector_access('net')
            add('eth0', net=net, mac=None,
                other=other_machine,
                other_interface="eth 2/1")

        @provides("other-machine")
        class OtherMachine(MachineModel):

            name = "switch.foo.com"

            class fooNetConfig(NetworkConfigModel):
                pass

        class TheMachine(MachineModel):

            name = "machine.foo.com"

            @setup_task("Generate me")
            def generate_stuff(self):
                nonlocal stuff_generated
                stuff_generated = True

            @generate_stuff.invalidator()
            def generate_stuff(self, **kwargs):
                return False

    stuff_generated = False
    l = await ainjector(Layout)
    await l.generate()
    assert stuff_generated
    assert l.TheMachine.network_links['eth0'].other.machine == l.OtherMachine
    assert len(l.net.network_links) == 2


@async_test
async def test_implicit_customization(ainjector):
    class Layout(ModelGroup):

        class foo(MachineModel):
            name = "foo.com"

            class cust(MachineCustomization):
                pass

    l = await ainjector(Layout)
    ainjector.add_provider(machine_implementation_key, dependency_quote(Machine))
    assert hasattr(l.foo.machine_type, 'cust_task')


def test_model_mixin(injector):
    class layout(CarthageLayout):

        @model_mixin_for(host="foo.com")
        class foomixin:
            bar = 42

        class foo(MachineModel):
            name = "foo.com"

    assert layout.foo.bar == 42


@async_test
async def test_local_networking(ainjector):
    class layout(ModelGroup):
        class net1(NetworkModel):
            pass

        class local(MachineModel):
            add_provider(machine_implementation_key, dependency_quote(LocalMachine))

            class nc(NetworkConfigModel):
                net1 = injector_access('net1')

                add('eth0', mac=None,
                    net=net1)
                add('br_net1', net=net1,
                    member='eth0',
                    local_type='bridge',
                    mac=None,
                    )

    l = await ainjector(layout)
    await l.generate()
    assert getattr(l.net1, 'bridge_name', None) == "br_net1"


@async_test
async def test_model_tasks(ainjector):
    class Layout(ModelGroup):
        class tasks(ModelTasks):

            @setup_task("frob stuff")
            def frob_stuff(self):
                nonlocal frobbed
                frobbed = True

            @frob_stuff.check_completed()
            def frob_stuff(self):
                return False

    frobbed = False
    l = await ainjector(Layout)
    assert frobbed is False
    await l.generate()
    assert frobbed is True


@async_test
async def test_tasks_inherit(ainjector):
    class template(MachineModel, template=True):

        class cust1(BaseCustomization):
            @setup_task("Task a")
            def task_1(self):
                nonlocal task_1_run
                task_1_run = True

            @task_1.check_completed()
            def task_1(self):
                return False

    class layout(ModelGroup):
        add_provider(machine_implementation_key, dependency_quote(LocalMachine))

        class local(template, MachineModel):

            class cust2(BaseCustomization):
                @setup_task("task 2")
                def task_2(self):
                    nonlocal task_2_run
                    task_2_run = True

                @task_2.check_completed()
                def task_2(self):
                    return False

    task_1_run = False
    task_2_run = False
    l = await ainjector(layout)
    l.local.machine
    assert task_2_run is False
    await l.local.machine.async_become_ready()
    assert task_2_run
    assert task_1_run
    assert l.local.cust2.task_2


@async_test
async def test_injector_xref_no_cycle(ainjector):
    class layout(ModelGroup):
        class local(MachineModel):
            add_provider(machine_implementation_key, dependency_quote(LocalMachine))

            class cust(BaseCustomization):

                async def async_ready(self):
                    await wait_future
                    return await super().async_ready()

    l = await ainjector(layout)
    wait_future = ainjector.loop.create_future()
    machine_key = InjectionKey("machine")
    l.injector.add_provider(
        machine_key,
        injector_xref(
            InjectionKey(
                MachineModel,
                host="local"),
            InjectionKey(Machine)))
    instantiation_future = ainjector.loop.create_task(l.ainjector.get_instance_async(machine_key))
    await asyncio.sleep(0.2)
    assert instantiation_future.done() is False
    l.local.machine
    wait_future.set_result(True)
    await instantiation_future


@async_test
async def test_globally_unique_with_name(ainjector):
    "Confirm that if @globally_unique shadows inject_by_name the result is globally unique"
    class outer(ModelGroup):
        @provides(InjectionKey("inner", role="role"))
        class inner(ModelGroup):
            @globally_unique_key("comcast_net")
            class comcast_net(NetworkModel):
                pass
    l = await ainjector(outer)
    ainjector = l.ainjector
    await ainjector.get_instance_async('comcast_net')
@async_test
async def test_task_ordering(ainjector):
    "Test that role tasks follow the order of python inheritance"
    class Layout(CarthageLayout):
        class first(MachineModel, template=True):
            class first_cust(BaseCustomization):
                @setup_task("Should run first")
                def first_task(self):
                    nonlocal first_run
                    first_run = True

        class second(MachineModel, template=True):

            class cust(BaseCustomization):
                @setup_task("Should run second")
                def second_task(self):
                    nonlocal second_run
                    assert first_run
                    second_run = True

        # Assume first and second are roles.  The left most (least
        # inherited) role should have its tasks run last
        class machine(second, first):
            pass

    first_run = False
    second_run = False
    ainjector.add_provider(Layout)
    ainjector.add_provider(machine_implementation_key, dependency_quote(LocalMachine))
    layout = await ainjector.get_instance_async(Layout)
    await layout.machine.machine.async_become_ready()
    assert first_run
    assert second_run

@async_test
async def test_async_injector_access(ainjector):
    class layout(CarthageLayout):
        @provides("async")
        class async_injectable(AsyncInjectable):

            async def async_ready(self):
                nonlocal async_ready
                async_ready = True
                await super().async_ready()



    async_ready = False
    l = await ainjector(layout)
    access = injector_access("async")
    await l.ainjector(access)
    assert async_ready is True

@async_test
async def test_detects_class_multi_instantiate(ainjector):
    with pytest.raises(SyntaxError):
        class layout(CarthageLayout):

            @provides("model")
            class a(InjectableModel):
                pass
            add_provider(InjectionKey("b"), a) #should raise

@async_test
async def test_model_subclass_propagation(ainjector):
    "Test that if a template includes classes to be propagated, subclasses of the template properly propagate those classes."
    class FirstLevel(InjectableModel): pass
    propagate_key(InjectionKey(FirstLevel), FirstLevel)
    @propagate_key(InjectionKey(FirstLevel, level=2))
    class SecondLevel(FirstLevel): pass
    
    class TemplateNetwork(NetworkModel):
        @propagate_key(InjectionKey(NetworkConfig))
        class net_config(NetworkConfigModel): pass

    class layout(CarthageLayout):
        @provides(InjectionKey(Network, role='public'))
        class public_network(TemplateNetwork):
            name = 'public_network'

        @provides(InjectionKey(ModelContainer, name='container'))
        class container(ModelContainer):
            class third(SecondLevel): pass
                  
    ainjector.add_provider(layout)
    l = await ainjector.get_instance_async(layout)
    nc = await l.ainjector.get_instance_async(InjectionKey(NetworkConfig, role='public'))
    l3 = await l.ainjector.get_instance_async(InjectionKey(FirstLevel, name='container'))
    

@async_test
async def test_machine_model_leakage(ainjector):
    '''
    Test that MachineModel does not leak keys in up-propagations unless those keys are explicitly propagated.
    Designed to confirm abug is not reintroduced.
    '''
    repository_key = InjectionKey(MachineModel, role='repository')
    class layout(CarthageLayout):
        
        class model1(MachineModel):
            @provides(repository_key)
            class server(MachineModel):
                pass

    l = await ainjector(layout)
    filter_result = l.injector.filter(MachineModel, ['role'])
    assert filter_result == []
    
