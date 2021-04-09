# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import pytest
from pathlib import Path

from carthage.pytest import *
from carthage.dependency_injection import *
from carthage import base_injector, Machine, setup_task, ConfigLayout
from carthage.modeling.base import *
from carthage.modeling.implementation import ModelingContainer
from carthage.modeling.decorators import *
from carthage.network import NetworkConfig, Network
from carthage.machine import MachineCustomization
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
    
def test_modeling_class_injection(injector):
        class Layout(InjectableModel):
            class nc(NetworkConfig): pass
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
                #This should break the chain because it is not a InjectableModel
                class MostInner(InjectableModel):
                    assert 'inner' not in locals().keys()
                    
    res = injector(Layout.inner)
    assert res.green == "blue"
    assert Layout.inner2.inner  is Layout.inner

        
            
def test_container(injector):
    class Foo(Injectable): pass
    def extra_container(domain):
        d = domain
        @provides(InjectionKey("included", constraint = 20))
        class Baz(ModelGroup):
            domain = d
            class server(MachineModel):
                name = "server"
        return Baz
    class Layout(InjectableModel, metaclass = ModelingContainer):

        add_provider(InjectionKey("key1"),
                     42)
        access_key_1 = injector_access("key1")
        
        net_config = injector_access(InjectionKey(NetworkConfig, domain = "evil.com"))
        class RedEnclave(Enclave):

            domain = "evil.com"

            @propagate_up()
            class nc(NetworkConfig): pass

            include_container(extra_container)

            @provides("site-network", InjectionKey(Network, name="red"))
            class SiteNetwork(NetworkModel):
                add_provider(InjectionKey(Foo), Foo, propagate = True)
                name = "red"
                
    res = injector(Layout)
    nc = res.injector.get_instance(InjectionKey(
        NetworkConfig, domain = "evil.com"))
    assert nc is Layout.RedEnclave.nc
    machine = res.injector.get_instance(InjectionKey(MachineModel, host = "server.evil.com"))
    assert isinstance(machine, MachineModel)
    assert res.net_config is res.RedEnclave.nc
    assert res.access_key_1 == 42
    foo = res.injector.get_instance(InjectionKey(
        Foo, domain = "evil.com", name = "red",
        _ready = False))
    assert isinstance(foo, Foo)
    
    
    
    
    
def test_dynamic_name(injector):
    class Layout(ModelGroup):

        for i in range(3):
            @dynamic_name(f'i{i+1}')
            class ignored(InjectableModel): square = i*i
    assert Layout.i3.square == 4
    
@async_test
async def test_machine_model(injector):
    class Layout(ModelGroup):

        class Red(Enclave):

            domain = "evil.com"

            class NetConfig (NetworkConfigModel):
                pass
            
            class Router(MachineModel): pass

            class task_machine(MachineModel):

                @setup_task("Frob the frobbables")
                def frob_stuff(self): pass

        red_router = injector_access(InjectionKey(MachineModel, host  = "router.evil.com"))

    res = injector(Layout)
    assert res.red_router.name == "router.evil.com"
    assert hasattr(res.Red.task_machine, 'setup_tasks')
    await res.Red.task_machine.async_become_ready()
    
    

@async_test
async def test_example_model(ainjector):
    from carthage.modeling.example import Layout
    res = await ainjector(Layout)
    nc = res.net_config
    assert isinstance(nc, NetworkConfigModel)
    samba = res.injector.get_instance(InjectionKey(MachineModel, host = "samba.evil.com"))
    assert samba.network_config is nc
    
def test_transclusion(injector):
    injector.add_provider(InjectionKey(MachineModel, host="moo.com"),
                          "bar")
    injector.add_provider(InjectionKey(Machine, host = "mar.com"),
                          "baz")
    class FirstLayout(ModelGroup):

        @model_mixin_for(host = "mar.com")
        class MarMixin(MachineModel):
            mixed_in = True

    first_layout = injector(FirstLayout)
    injector = first_layout.injector

    @transclude_injector(injector)
    class Layout(ModelGroup):

        @transclude_overrides()
        class moo(MachineModel):
            name = "moo.com"

        class mar(*injector(model_bases, MachineModel, "mar.com")):
            name = "mar.com"
            

    l = injector(Layout)
    assert l.moo == "bar"
    assert l.mar.machine == "baz"
    assert l.mar.mixed_in == True
    

@async_test
async def test_generate_and_network(ainjector):


    class Layout(ModelGroup):

        class net(NetworkModel):

            name = "the-net"
            
        class nc(NetworkConfigModel):
            other_machine = injector_access("other-machine")

            add('eth0', net = net, mac = None,
                other = other_machine,
                other_interface = "eth 2/1")

        @provides("other-machine")
        class OtherMachine(MachineModel):

            name = "switch.foo.com"

            class fooNetConfig(NetworkConfigModel): pass

        class TheMachine(MachineModel):

            name = "machine.foo.com"

            @setup_task("Generate me")
            def generate_stuff(self):
                nonlocal stuff_generated
                stuff_generated = True
            @generate_stuff.invalidator()
            def generate_stuff(self):
                return False
            
                
    stuff_generated = False
    l = ainjector.injector(Layout)
    await l.generate()
    assert stuff_generated
    assert l.TheMachine.network_links['eth0'].other.machine == l.OtherMachine
    assert len(l.net.network_links) == 2
    
def test_implicit_customization(injector):
    class Layout(ModelGroup):

        class foo(MachineModel):
            name = "foo.com"
            class cust(MachineCustomization):
                pass

    l = injector(Layout)
    injector.add_provider(machine_implementation_key, dependency_quote(Machine))
    assert hasattr(l.foo.machine_type, 'model_customization')
    
