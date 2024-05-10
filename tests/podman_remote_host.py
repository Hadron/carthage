# Copyright (C) 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

'''
A container host running in AWS for the podman tests.  In its own separate file because of all the dependencies.
'''
from carthage import *
from carthage.modeling import *
from carthage import become_privileged
from carthage import cloud_init as ci
import carthage.ansible

try:
    import carthage_aws as aws
except ImportError:
    raise ImportError('For --remote-container-host to work, your carthage config needs to reference the AWS plugin and include valid AWS account and VPC information.')

class container_host(MachineModel, AsyncInjectable):
    add_provider(machine_implementation_key, dependency_quote(aws.AwsVm))
    add_provider(ssh_jump_host, dependency_quote(None))
    ssh_login_user = 'admin'
    runas_user = 'poduser'
    machine_mixins = (become_privileged.BecomePrivilegedMixin, carthage.ansible.AnsibleIpAddressMixin)
    cloud_init = True
    add_provider(ci.DisableRootPlugin)
    aws_instance_type = 't3.medium'
    aws_availability_zone = 'us-east-1a'

    add_provider(InjectionKey('aws_ami'), aws.image_provider(owner=aws.debian_ami_owner, name='debian-12-amd64-*'))

    @provides('aws_net')
    class aws_net(NetworkModel):
        v4_config = V4Config(
            network='192.168.100.0/24')

        class ssh_sg(aws.AwsSecurityGroup):
            name = 'ssh'
            ingress_rules = (
                aws.SgRule(
                    cidr='0.0.0.0/0',
                    port=22),
                )
        aws_security_groups = ['ssh']
        
    
    class net_config(NetworkConfigModel):
        add('eth0', mac=None, net=aws_net)
        
    class install_podman(FilesystemCustomization):
        runas_user = 'root'

        @setup_task("Install podman software")
        async def do_install(self):
            await self.run_command('apt', 'update')
            await self.run_command('apt', '-y',
                                   'install',
                                   'podman', 'containers-storage', 'acl')
            await self.run_command('useradd', '-m', '-s', '/bin/bash', 'poduser')
            await self.run_command('loginctl', 'enable-linger', 'poduser')
            
