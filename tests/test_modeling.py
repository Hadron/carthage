import pytest
from carthage.pytest import *
from carthage.dependency_injection import *
from carthage import base_injector, Machine
from carthage.modeling.base import *
from carthage.modeling.implementation import ModelingContainer
from carthage.modeling.decorators import *
from carthage.network import NetworkConfig, Network

@pytest.fixture()
def injector():
    injector = base_injector(Injector)
    injector.claim()
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
    
def test_machine_model(injector):
    class Layout(ModelGroup):

        class Red(Enclave):

            domain = "evil.com"

            class NetConfig (NetworkConfigModel):
                pass
            
            class Router(MachineModel): pass

        red_router = injector_access(InjectionKey(MachineModel, host  = "router.evil.com"))

    res = injector(Layout)
    assert res.red_router.name == "router.evil.com"
    
    

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

    @transclude_injector(injector)
    class Layout(ModelGroup):

        @transclude_overrides()
        class moo(MachineModel):
            name = "moo.com"

        class mar(MachineModel):
            name = "mar.com"
            

    l = injector(Layout)
    assert l.moo == "bar"
    assert l.mar.machine == "baz"
    
