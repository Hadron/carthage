
class RouterConfig(NetworkConfigModel):

    internet = injector_access("internet")
    site_network = injector_access("site-network")
    add(internet, 'eth0', None)
    add(site_network, 'eth1', None)
    
class Layout(ModelGroup):

    class net_config(NetworkConfigModel):
        site_network = injector_access("site-network")
        add(site_network, 'eth0', None)
        
    class Internet(NetworkModel):

        bridge_name = "brint"
        
    class Red(Enclave):
        domain = "evil.com"

        @provides("site-network")
        class RedNet(ModelingNetwork):
            pass


        class router(MachineModel):
            add_provider(NetworkConfig, RouterConfig)

        class samba(MachineModel):
            add_ansible_role("samba")
            for u in ('george', 'sue', 'pat'):
                @dynamic_name(f'{u}_desktop')
                class desktop(MachineModel):
                    name = f'${u}-desktop'
                    
