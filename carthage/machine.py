# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, os, os.path
from .dependency_injection import *
from .config import ConfigLayout
from .ssh import SshKey, SshAgent
from .utils import memoproperty
from . import sh
import carthage.ssh
from .setup_tasks import SetupTaskMixin, setup_task
class MachineRunning:

    async def __aenter__(self):
        self.machine.with_running_count +=1
        if self.machine.running:
            return
        try:
            await self.machine.start_machine()
            return

        except:
            self.machine.with_running_count -= 1
            raise

    async def __aexit__(self, exc, val, tb):
        self.machine.with_running_count -= 1
        if self.machine.with_running_count <= 0:
            self.machine_with_running_count = 0
            await self.machine.stop_machine()


    def __init__(self, machine):
        self.machine = machine

ssh_origin = InjectionKey('ssh-origin')
class SshMixin:
    '''An Item that can be sshed to.  Will look for the ssh_origin
    injection key.  If found, this should be a container.  The ssh will be
    launched from within the network namespace of that container in order
    to reach the appropriate devices.  Requires ip_address to be made
    available.  Requires an carthage.ssh.SshKey be injectable.
    '''

    class ip_address:

        def __get__(self, instance, owner):
            if instance is None: return self
            raise NotImplementedError
    ip_address = ip_address()
    ssh_options = ('-oStrictHostKeyChecking=no', )

    @memoproperty
    def ssh(self):
        try:
            ssh_origin_container = self.injector.get_instance(ssh_origin)
        except KeyError:
            ssh_origin_container = self if isinstance(self, Container) else None
        ssh_key = self.injector.get_instance(carthage.ssh.SshKey)
        options = self.ssh_options + ('-oUserKnownHostsFile='+os.path.join(self.config_layout.state_dir, 'ssh_known_hosts'),)
        if ssh_origin_container is not None:
            ip_address = self.ip_address
            if self is ssh_origin_container: ip_address = "127.0.0.1"
            leader = ssh_origin_container.container_leader
            ssh_origin_container.done_future().add_done_callback(self.ssh_recompute)
            return sh.nsenter.bake('-t', str(leader), "-n",
                                   "/usr/bin/ssh",
                              "-i", ssh_key.key_path,
                                   *options,
                                   ip_address,
                                   _env = ssh_key.agent.agent_environ)
        else:
            return sh.ssh.bake('-i', ssh_key.key_path,
                               *options, self.ip_address,
                               _env = ssh_key.agent.agent_environ)

    async def ssh_online(self):
        online = False
        for i in range(30):
            try: await self.ssh('date',
                                _bg = True, _bg_exc = False,
                                _timeout = 5)
            except (sh.TimeoutException, sh.ErrorReturnCode):
                await asyncio.sleep(1)
                continue
            online = True
            break
        if not online:
            raise TimeoutError("{} not online".format(self.ip_address))
        
    def ssh_recompute(self, *args):
        try:
            del self.__dict__['ssh']
        except KeyError: pass

    @classmethod
    def clear_ssh_known_hosts(cls, config_layout):
        try: os.unlink(
                os.path.join(config_layout.state_dir, "ssh_known_hosts"))
        except FileNotFoundError: pass

    def ssh_rekeyed(self):
        "Indicate that this host has been rekeyed"
        try:
            self.ip_address
        except NotImplementedError: return
        try: sh.ssh_keygen(
                "-R", self.ip_address,
                f=os.path.join(self.config_layout.state_dir, "ssh_known_hosts"))
        except sh.ErrorReturnCode: pass
        
        
class Machine(AsyncInjectable, SshMixin):

    '''
    Represents a machine that can be interacted with.

    This is an abstract class representing the interface to machines independent of the technology being used to control them.  This class can be used for:

    * :class:`Locally run containers <carthage.container.Container>`

    * :class:`Local VMs <carthage.vm.Vm>` using KVM

    * Vmware :class:`~carthage.vmware.vm.Vm`
    * Containers or VMs run on other infrastructure.

    The main capabilities of this interface are to be able to start and stop machines, know their IP address, and connect via ssh.

    '''
    def __init__(self, name, injector, config_layout):
        super().__init__(injector = injector)
        self.name = name
        self.config_layout = config_layout
        self.injector = injector.copy_if_owned().claim()
        self.ainjector = self.injector(AsyncInjector)
        self.machine_running = MachineRunning(self)
        self.with_running_count = 0


    @property
    def full_name(self):
        return self.config_layout.container_prefix+self.name

    async def start_dependencies(*args, **kwargs):
        '''Interface point that should be called by :meth:`start_machine` to start any dependent machines such as routers needed by this machine.'''
        pass
    

    def start_machine(self):
        '''
        Must be overridden.  Start the machine.
        '''
        raise NotImplementedError

    def stop_machine(self):
        ''' Must be overridden; stop the machine.
        '''
        raise NotImplementedError

    def __repr__(self):
        res =  f"<{self.__class__.__name__} name:{self.name} "
        try:
            res += f"ip_address:{self.ip_address}"
        except Exception: pass
        res += ">"
        return res

    async def apply_customization(self, cust_class, method = 'apply'):
        '''
        Apply a :class:`BaseCustomization` to this machine..
        '''
        customization = await self.ainjector(cust_class)
        meth = getattr(customization, method)
        return await meth()
    
@inject(injector = Injector,
        config_layout = ConfigLayout)
class BaseCustomization(SetupTaskMixin, AsyncInjectable):

    def __init__(self, apply_to: Machine, *,
                 injector, config_layout, **kwargs):
        self.injector = injector.copy_if_owned().claim()
        self.ainjector = self.injector(AsyncInjector)
        self.config_layout = config_layout
        self.host = apply_to
        super().__init__(**kwargs)
    async def async_ready(self):
        # We do not run setup tasks on construction.
        return self

    @property
    def customization_context(self):
        '''Can be overridden; a context manager in which customization tasks should be run
'''
        pass

    @property
    def stamp_path(self):
        return self.host.stamp_path

    async def last_run(self):
        '''
        :return: the most recent time any setup task on this Customization has run against the given host. Returns false if the tasks definitely need to run.
        '''
        last_run = 0.0
        for t in self.setup_tasks:
            run_task, last_run = await t.should_run_task(self, self.ainjector, last_run)
            if run_task:
                return False #We're a check_completed function not a should_run function
        return last_run

    async def apply(self):
        ''' Run setup tasks against host'''
        return await self.ainjector(self.run_setup_tasks, context = self.customization_context)

    def __getattr__(self, a):
        if a in ('ssh', 'ip_address', 'start_machine', 'stop_machine',
                 'name', 'full_name'):
            return getattr(self.host, a)
        raise AttributeError

    def __repr__(self):
        return f"<{self.__class__.__name__} description:\"{self.description}\" for {self.host.name}>"
    
    #: A description of the customization for inclusion in task logging
    description = ""

class MachineCustomization(BaseCustomization):

    '''A customization class for running customizations on running machines.'''
    
    @property
    def customization_context(self):
        return self.host.machine_running

class ContainerCustomization(BaseCustomization):

    '''A customization class for running tasks on :class:`~carthage.container.Container` instances or :class:`~carthage.image.ImageVolume` instances without actually booting the container.  This is valuable for tasks used in image production that want to manipulate the filesystem.
'''

    def __init__(self, apply_to, injector, config_layout):
        from .container import Container
        if not isinstance(apply_to, Container):
            raise TypeError(f'{self.__class__.__name__} can only be applied to Containers or ImageVolumes')
        super().__init__(apply_to = apply_to, injector = injector, config_layout = config_layout)

    @property
    def path(self):
        return self.host.volume.path
    
    def __getattr__(self, a):
        if a in ('container_command', ):
            return getattr(self.host, a)
        else: return super().__getattr__(a)
        

    
def customization_task    (c: BaseCustomization):
    '''
    :return: a setup_task for using a particular :class:`Customization` in a given :class:`Machine`.

    Usage::

        # in a machine
        add_packages = customization_task(AddOurPackagesCustomization)

    '''
    @setup_task(c.description)
    @inject(ainjector = AsyncInjector)
    async def do_task(machine, ainjector):
        await machine.apply_customization(c)

    @do_task.check_completed()
    @inject(ainjector = AsyncInjector)
    async def do_task(machine, ainjector):
        return await machine.apply_customization(c, method = "last_run")
    return do_task


            

__all__ = ['Machine', 'MachineRunning', 'SshMixin', 'Customization']
