# Copyright (C) 2018, 2019, 2020, 2021, 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import abc
import asyncio
import contextlib
import os
import os.path
import shlex
import tempfile
import typing
from pathlib import Path

from .dependency_injection import *
from .config import ConfigLayout
from .ssh import SshKey, SshAgent, RsyncPath
from .utils import memoproperty
from . import sh
import carthage.ssh
from .setup_tasks import SetupTaskMixin, setup_task, TaskWrapperBase, TaskInspector
import logging
logger = logging.getLogger("carthage")


class MachineRunning:

    async def __aenter__(self):
        if self.machine.with_running_count <= 0:
            if self.machine.running is None:
                await self.machine.is_machine_running()
            self.machine.already_running = self.machine.running
        self.machine.with_running_count += 1
        if self.machine.running:
            if self.machine._ssh_online_required and self.ssh_online:
                await self.machine.ssh_online()
            return
        try:
            await self.machine.start_machine()
            if self.ssh_online:
                await self.machine.ssh_online()
            return

        except BaseException:
            self.machine.with_running_count -= 1
            raise

    async def __aexit__(self, exc, val, tb):
        self.machine.with_running_count -= 1
        if self.machine.with_running_count <= 0 and not self.machine.already_running:
            self.machine.with_running_count = 0
            await self.machine.stop_machine()

    def __init__(self, machine, *,
                 ssh_online=None):
        self.machine = machine
        if ssh_online is None:
            ssh_online = machine.machine_running_ssh_online
        self.ssh_online = ssh_online


ssh_origin = InjectionKey('ssh-origin')
ssh_origin_vrf = InjectionKey("ssh-origin-vrf")


class SshMixin:
    '''An Item that can be sshed to.  Will look for the ssh_origin
    injection key.  If found, this should be a container.  The ssh will be
    launched from within the network namespace of that container in order
    to reach the appropriate devices.  Requires ip_address to be made
    available.  Requires an carthage.ssh.SshAgent be injectable.
    '''

    _ssh_online_required = True

    @memoproperty
    def ip_address(self):
        '''The IP address or name at which this machine should be managed.'''
        try:
            return self.model.ip_address
        except AttributeError:
            raise NotImplementedError from None

    @memoproperty
    def ssh_options(self):
        if hasattr(self.model, 'ssh_options'):
            return self.model.ssh_options
        return ('-lroot', )

    @memoproperty
    def ssh_online_retries(self):
        if hasattr(self.model, 'ssh_online_retries'):
            assert isinstance(self.model.ssh_online_retries, int), "ssh_online_retries must be `int`"
            return self.model.ssh_online_retries
        return 60

    @memoproperty
    def ssh_online_timeout(self):
        if hasattr(self.model, 'ssh_online_timeout'):
            assert isinstance(self.model.ssh_online_timeout, int), "ssh_online_timeout must be `int`"
            return self.model.ssh_online_timeout
        return 5

    @memoproperty
    def ssh(self):
        from .network import access_ssh_origin
        try:
            ssh_origin_container = self.injector.get_instance(InjectionKey(ssh_origin, _optional=True))
        except InjectionFailed:
            from .container import Container
            ssh_origin_container = self if isinstance(self, Container) else None
        ssh_key = self.injector.get_instance(InjectionKey(carthage.ssh.SshKey, _optional=True))
        if ssh_key:
            ssh_agent = ssh_key.agent
            if ssh_key.key_path:
                key_options = ("-i", ssh_key.key_path,)
            else:
                key_options = ('',)
        else:
            ssh_agent = self.injector.get_instance(carthage.ssh.SshAgent)
            key_options = tuple()
        options = self.ssh_options + ('-oUserKnownHostsFile=' +
                                      os.path.join(self.config_layout.state_dir, 'ssh_known_hosts'),)
        if ssh_origin_container is not None:
            ip_address = self.ip_address
            ssh_origin_container.done_future().add_done_callback(self.ssh_recompute)
            return self.injector(access_ssh_origin).bake(
                "/usr/bin/ssh",
                *key_options,
                *options,
                *self.config_layout.global_ssh_options.split(),
                ip_address,
                _env=ssh_agent.agent_environ)
        else:
            return sh.ssh.bake(*key_options,
                               *options,
                               *self.config_layout.global_ssh_options.split(),
                               self.ip_address,
                               _env=ssh_agent.agent_environ)

    def rsync(self, *args):
        '''
        Call rsync with given arguments.
        An argument may be a :class:`.RsyncPath` generated by :meth:`rsync_path`.  Such a path encapsulates a host name and a path.  When *rsync* is called, Carthage
        finds the appropriate ssh_origin to select the right namespace for rsync.

        Typical usage::

            await machine.rsync("file",
                rsync_path("/etc/script")
            #Copy file to /etc/script on machine
        '''
        ssh_key = self.injector.get_instance(SshKey)
        return ssh_key.rsync(*args)

    def rsync_path(self, p):
        '''
A marker in a call to :meth:`rsync` indicating that *p* should be copied to or from *self*.  Interacts with the Carthage rsync machinery to select the right network namespace.
        '''
        return RsyncPath(self, p)

    #: The command run remotely by :meth:`ssh_online`
    ssh_online_command = 'date'

    async def ssh_online(self):
        logger.debug(f'Waiting for {self.name} to be ssh_online')
        online = False
        last_error = None
        await self.ainjector.get_instance_async(InjectionKey(carthage.ssh.SshKey, _optional=True)) #Instantiate in case it is async
        await self.ainjector.get_instance_async(carthage.ssh.SshAgent)
        for i in range(self.ssh_online_retries):
            try:
                await self.ssh(self.ssh_online_command,
                               _bg=True, _bg_exc=False,
                               _timeout=self.ssh_online_timeout)
            except (sh.TimeoutException, sh.ErrorReturnCode) as e:
                last_error = e
                await asyncio.sleep(1)
                continue
            online = True
            last_error = None
            self._ssh_online_required = False
            logger.debug(f'{self.name} is ssh_online')
            break
        if not online:
            raise TimeoutError("{} not online".format(self.ip_address)) from last_error

    def ssh_recompute(self, *args):
        try:
            del self.__dict__['ssh']
        except KeyError:
            pass
        self._ssh_online_required = True

    @classmethod
    def clear_ssh_known_hosts(cls, config_layout):
        try:
            os.unlink(
                os.path.join(config_layout.state_dir, "ssh_known_hosts"))
        except FileNotFoundError:
            pass

    def ssh_rekeyed(self):
        "Indicate that this host has been rekeyed"
        try:
            self.ip_address
        except NotImplementedError:
            return
        try:
            sh.ssh_keygen(
                "-R", self.ip_address,
                f=os.path.join(self.config_layout.state_dir, "ssh_known_hosts"))
        except sh.ErrorReturnCode:
            pass

class ResolvableModel(Injectable):

    '''
    In a :class:`carthage.modeling.CarthageLayout` all the models are instantiated as part of bringing the layout to ready.  This permits models to contribute to a shared understanding of networking and other global aspects of the layout.

    This class represents  a model that can be instantiated and *resolved*.  A layout will instantiate all :class:`ResolvableModel` objects in the scope of its injector that have a name constraint on their :class:`InjectionKey`.

    This class is defined here rather than in the modeling layer so that :class:`AbstractMachineModel` does not need to depend on the modeling layer.

    Subclasses of this model will typically need to override default_class_injection_key and probably supplimentary_injection_keys.
    
    '''

    async def resolve_model(self, force):
        pass

#: If this key is provided in the injector context of a :class:`NetworkedModel`, then that model assumes it is in the namespace of the :class:`NetworkedModel` or :class:`Machine` providing this key.  Rather than resolving the network config, the *network_links* property of the object providing this key is reused.
network_namespace_key = InjectionKey('carthage.machine.network_namespace', _ready=False)

class NetworkedModel(ResolvableModel):

    '''Represents something like a :class:`AbstractMachineModel` or a :class:`carthage.podman.PodmanPod` that generates a set of network_links from a :class:`~NetworkConfig`.

    When :meth:`resolve_networking` is called, if *self.injector* provides :ref:`network_namespace_key`,  then the network_links are reused from the object providing that dependency.  Typical usage is for a :class:`~carthage.oci.OciPod` or similar network namespace in which a :class:`AbstractMachineModel` will be run to provide *network_namespace_key*.
    

    '''

    network_links: typing.Mapping[str, carthage.network.NetworkLink]

    async def resolve_networking(self, force: bool = False):
        '''
            Adds all :class:`carthage.network.NetworkLink` objects specified in the :class:`carthage.network.NetworkConfig`  to the network_links property.

        :param force: if True, resolve the network config even if it has already been resolved once.

        '''
        from carthage.network import NetworkConfig
        if not force and self.network_links:
            return
        try:
            if hasattr(self, 'ainjector'):
                ainjector = self.ainjector
            else:
                ainjector = self.injector(AsyncInjector)
            network_namespace = await ainjector.get_instance_async(InjectionKey(network_namespace_key, _optional=True, _ready=False))
            if network_namespace and network_namespace is not self:
                await network_namespace.resolve_networking(force=force)
                self.network_links = network_namespace.network_links
                return
            network_config = await ainjector.get_instance_async(NetworkConfig)
        except KeyError:
            return
        if network_config is None:
            return
        result = await ainjector(network_config.resolve, self)

    async def resolve_model(self, force:bool=False):
        await self.resolve_networking(force=force)
        return await super().resolve_model(force=force)
    

class AbstractMachineModel(NetworkedModel):

    '''
    Represents properties of a machine that do not involve interacting with an implementation of that machine.  All the *AbstractMachineModels* in a layout can be instantiated to reason about things like network connections, configuration, and what machines will be built without instantiating any of the machines.  Typically if a :class:`Machine` has a model, the model will be made available either by setting the *model* property on the machine, or by providing a dependency for :class:`AbstractModel` in the injector in which the machine is instantiated.

    The most common concrete implementation of a machine model is :class:`carthage.modeling.MachineModel`.

    '''
    
    name: str

    #: If True, :meth:`Machine.start_dependencies()` will stop collecting dependencies at the injector of this model.  In the normal situation where the :class:`Machine` is instantiated within the model's dependency context, what this means is that  only system dependencies declared on the model will be started.  This may also be an :class:`InjectionKey`, an :class:`Injector`, or an :class:`Injectable`.  Se the documentation of :meth:`Machine.start_dependencies()`.
    override_dependencies: typing.Union[bool, Injector, Injectable, InjectionKey] = False

    @classmethod
    def default_class_injection_key(self):
        if hasattr(self, 'name'):
            return InjectionKey(AbstractMachineModel, host=self.name)
        else:
            return super().default_class_injection_key()

    @classmethod
    def supplementary_injection_keys(cls, k):
        name = None
        if 'host' in k.constraints:
            name =k.constraints['host']
        elif 'name' in k.constraints:
            name = k.constraints['name']
        if name:
            yield InjectionKey(ResolvableModel, name=name)
        yield from super().supplementary_injection_keys(k)
        


@inject_autokwargs(config_layout=ConfigLayout)
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

    model: typing.Optional[AbstractMachineModel]
    name: str
    network_links: typing.Mapping[str, carthage.network.NetworkLink]

    #: Should machine_running default to calling ssh_online
    machine_running_ssh_online: bool = True

    def __init__(self, name=None, **kwargs):
        super().__init__(**kwargs)
        if name is not None:
            self.name = name
        else:
            if not hasattr(self, 'name'):
                raise TypeError(f'name must be supplied to constructor or set in the class')
        self.with_running_count = 0
        self.already_running = False
        self.sshfs_count = 0
        self.sshfs_lock = asyncio.Lock()
        self.injector.add_provider(InjectionKey(Machine), self)
        if not hasattr(self, 'model'):
            self.model = None
        self.running = None

    def machine_running(self, **kwargs):
        '''Returns a asynchronous context manager; within the context manager, the machine is expected to be running unless :meth:`stop_machine` is explicitly called.
'''
        return MachineRunning(self, **kwargs)

    @property
    def full_name(self):
        return self.config_layout.container_prefix + self.name

    @memoproperty
    def network_links(self):
        if self.model:
            return self.model.network_links
        return {}

    #: If true, use self.filesystem_access for rsync, otherwise use ssh.
    rsync_uses_filesystem_access = False

    async def start_dependencies(self):
        '''Interface point that should be called by :meth:`start_machine` to start any dependent machines such as routers needed by this machine.

         Default behavior is provided for machines with :class:`AbstractMachineModels` attached to thennn *model* property.  In this case,  any providers of ``InjectionKey(SystemDependency)`` with a name constraint are collected.  These objects are called  with an AsyncInjector.  For example :class:`carthage.system_dependency.MachineDependency` will start some other machine and optionally wait for it to become online.

        In typical usage, the *Machine* is contained in the injector
        context of its model.  Dependencies for the current machine
        may be added directly to its model's injector.  Dependencies
        shared among a group of machines may be added to injector
        contexts that contain the model.  For example::

            network.injector.add_provider(MachineDependency("router.network"))
            # But the fileserver also needs the domain controller
            network.fileserver_model.injector.add_provider(MachineDependency("domain-controller.network"))

        But in such a situation, the router itself should not depend on itself.  Two approaches are possible.  The first is to mask out the dependency in the router's model::

            ignore_system_dependency(network.router_model.injector, MachineDependency("router.network"))

        Another mechanism is available: the *override_dependencies* property of :class:`AbstractMachineModel`.  This property controls how far up the injector chain to look for dependencies:

        true
            Only consider dependencies directly defined on the model or the Machine.  Does not work correctly if the *self.injector* is not in the injector context of *self.model.injector*.

        an :class:`~Injector`
            Filter out dependencies declared between the parent of the model injector and the provided injector, inclusive.  So, consider a machine in a network in an enclave.  If the enclave's injector is provided, then injectors declared on the enclave and network will be ignored, but dependencies declared at a level larger than the enclave will still be started.

        an :class:`~Injectable`
            Filter out dependencies between the machine model and the injector contained in the Injectable inclusive.

        an :class:`~InjectionKey`
            Instantiate the key, and assume it is an Injectable.  Treat as that injectable

        For finer grain control, implementations can override this method.

        '''
        from carthage.system_dependency import MachineDependency, SystemDependency

        def callback(d):
            def cb(future):
                try:
                    future.result()
                except BaseException:
                    logger.exception(f"Error resolving {d}:")
            return cb

        def filter_dependencies(k):
            if 'name' not in k.constraints:
                return False
            if k in filter_keys:
                return False
            return True
        if not hasattr(self, 'model'):
            return
        logger.debug(f'Starting dependencies for {self.name}')
        stop_at = None
        model = self.model
        override_dependencies = model and model.override_dependencies
        if override_dependencies is True:
            stop_at = model.injector
            filter_keys = tuple()
        elif not override_dependencies:
            filter_keys = tuple()
        else:
            if isinstance(override_dependencies, InjectionKey):
                override_dependencies = await self.ainjector.get_instance_async(InjectionKey(override_dependencies, _ready=False))
            if isinstance(override_dependencies, Injectable):
                override_dependencies = override_dependencies.injector
            if not isinstance(override_dependencies, Injector):
                raise TypeError(
                    "override_dependencies must be boolean, an InjectionKey resolving to an INjectable, an Injectable, or an injector")
            filter_keys = model.injector.parent_injector.filter(
                SystemDependency, ['name'],
                stop_at=override_dependencies)
            logger.debug("Ignoring dependencies: %s", " ".join([k.name for k in filter_keys]))

        results = await self.ainjector.filter_instantiate_async(SystemDependency, filter_dependencies, ready=True, stop_at=stop_at)
        futures = []
        for k, d in results:
            future = asyncio.ensure_future(d(self.ainjector))
            future.add_done_callback(callback(d))
            futures.append(future)
        await asyncio.gather(*futures)

    def setup_task_event_keys(self):
        return self.supplementary_injection_keys(InjectionKey(Machine, host=self.name))
    
    async def start_machine(self):

        '''
        Must be overridden.  Start the machine.
        '''
        self.injector.emit_event(InjectionKey(Machine),
                                 "start_machine", self,
                                 adl_keys={InjectionKey(Machine, host=self.name)} |
                                 set(self.supplementary_injection_keys(InjectionKey(Machine, host=self.name))))

    async def stop_machine(self):
        ''' Must be overridden; stop the machine.
        '''
        self.injector.emit_event(InjectionKey(Machine),
                                 "stop_machine", self,
                                 adl_keys={InjectionKey(Machine, host=self.name)} |
                                 set(self.supplementary_injection_keys(InjectionKey(Machine, host=self.name))))
        self._ssh_online_required = True

    async def is_machine_running(self):
        '''
:return: Whether the machine is running

        Probe whether the machine is running and set self.running appropriately.  It is most important that running be set accurately when :meth:`start_machine` and :meth:`stop_machine` can start or stop the machine.  For :class:`BareMetalMachine` it is reasonable to assume that the machine is running.  This interface point should not call :meth:`ssh_online` or confirm the machine is on the network.
        '''
        raise NotImplementedError

    def __repr__(self):
        res = f"<{self.__class__.__name__} name:{self.name} "
        try:
            res += f"ip_address:{self.ip_address}"
        except Exception:
            pass
        res += ">"
        return res

    async def resolve_networking(self, force: bool = False):
        '''
        Adds all :class:`carthage.network.NetworkLink` objects specified in the :class:`carthage.network.NetworkConfig`  to the network_links property.

        :param force: if True, resolve the network config even if it has already been resolved once.

        '''
        from carthage.network import NetworkConfig
        if not force and self.network_links:
            return
        try:
            network_config = await self.ainjector.get_instance_async(NetworkConfig)
        except KeyError:
            return
        if network_config is None:
            return
        result = await self.ainjector(network_config.resolve, self.model or self)

    async def apply_customization(self, cust_class, method='apply', **kwargs):
        '''
        Apply a :class:`BaseCustomization` to this machine..

        :parameter stamp: A distinguisher for stamps created by the customization.  The stamp will include this value as well as the stamp from the :func:`setup_task`.

        '''
        customization = await self.ainjector(cust_class, apply_to=self, **kwargs)
        meth = getattr(customization, method)
        return await meth()

    def _apply_to_filesystem_customization(self, customization):
        '''
        Adapts the customization to this type of machine.  Overridden in machines that can customize a filesystem without booting.
        '''
        customization.customization_context = customization._machine_context()
        customization.run_command = self.ssh_run_command

    def ssh_run_command(self,
                        *args,
                        _bg=True,
                        _bg_exc=False):
        '''
            Ssh has really bad quoting; it effectively  removes one level of quoting from the input.
This handles quoting and  makes sure each argument is a separate argument on the eventual shell;
it works like :meth:`carthage.container.Container.container_command` and is used to give a consistent interface by :meth:`FilesystemCustomization.run_command`.
'''
        return self.ssh(
            shlex.join(args),
            _bg=_bg, _bg_exc=_bg_exc)

    async def sshfs_process_factory(self):
        return sh.sshfs(
            '-o' 'ssh_command=' + " ".join(
                str(self.ssh).split()[:-1]),
            f'{self.ip_address}:/',
            self.sshfs_path,
            '-f',
            _bg=True,
            _bg_exc=False)

    @contextlib.asynccontextmanager
    async def filesystem_access(self):
        '''
        An asynchronous context manager that makes the filesystem of the *Machine* available on a local path.

        :returns: Path at which the filesystem can be accessed while in the context.

        '''
        async with self.machine_running(ssh_online=True):
            self.sshfs_count += 1
            try:
                # Argument for correctness of locking.  The goal of
                # sshfs_lock is to make sure that two callers are not both
                # trying to spin up sshfs at the same time.  The lock is
                # never held when sshfs_count is < 1, so it will not block
                # when the coroutine that actually starts sshfs acquires
                # the lock.  Therefore the startup can actually proceed.
                # It would be equally correct to grab the lock before
                # incrementing sshfs_count, but more difficult to
                # implement because the lock must be released by time of
                # yield so other callers can concurrently access the filesystem.
                async with self.sshfs_lock:
                    if self.sshfs_count == 1:
                        self.sshfs_path = tempfile.mkdtemp(
                            dir=self.config_layout.state_dir, prefix=self.name, suffix="sshfs")
                        self.sshfs_process = await self.sshfs_process_factory()
                        for x in range(5):
                            alive, *rest = self.sshfs_process.process.is_alive()
                            if not alive:
                                await self.sshfs_process
                                raise RuntimeError  # I'd expect that to have happened from an sh exit error already
                            if os.path.exists(os.path.join(
                                    self.sshfs_path, "run")):
                                break
                            await asyncio.sleep(0.4)
                        else:
                            raise TimeoutError("sshfs failed to mount")
                yield Path(self.sshfs_path)
            finally:
                self.sshfs_count -= 1
                if self.sshfs_count <= 0:
                    self.sshfs_count = 0
                    try:
                        self.sshfs_process.process.terminate()
                    except BaseException:
                        pass
                    dir = self.sshfs_path
                    self.sshfs_path = None
                    self.sshfs_process = None
                    await asyncio.sleep(0.2)
                    with contextlib.suppress(OSError):
                        os.rmdir(dir)


@inject_autokwargs(config_layout=ConfigLayout)
class BaseCustomization(SetupTaskMixin, AsyncInjectable):

    def __init__(self, apply_to: Machine,
                 stamp=None, **kwargs):
        if isinstance(apply_to, BaseCustomization):
            apply_to = apply_to.host
        self.host = apply_to
        if not getattr(self, 'description', None):
            self.description = self.__class__.__name__
        self.stamp_stem = stamp or self.__class__.__name__
        super().__init__(**kwargs)

    @classmethod
    def default_class_injection_key(cls):
        description = cls.description or cls.__name__
        return InjectionKey(cls, description=description)

    # We do not run setup_tasks on construction
    async_ready = AsyncInjectable.async_ready

    @memoproperty
    @contextlib.asynccontextmanager
    async def customization_context(self):
        '''Can be overridden; context in which customization tasks are run.
'''
        try:
            yield
        finally:
            pass

    @property
    def stamp_path(self):
        return self.host.stamp_path

    def create_stamp(self, stamp, contents):
        stamp = f'{self.stamp_stem}-{stamp}'
        return self.host.create_stamp(stamp, contents)

    def check_stamp(self, stamp):
        stamp = f'{self.stamp_stem}-{stamp}'
        return self.host.check_stamp(stamp)

    def delete_stamp(self, stamp):
        stamp = f'{self.stamp_stem}-{stamp}'
        return self.host.delete_stamp(stamp)

    def inspect_setup_tasks(self):
        return super().inspect_setup_tasks(
            stamp_stem=self.stamp_stem+'-', instance_id=id(self.host))
    
    async def last_run(self):
        '''
        :return: the most recent time any setup task on this Customization has run against the given host. Returns false if the tasks definitely need to run.
        '''
        last_run = 0.0
        for t in self.setup_tasks:
            run_task, last_run = await t.should_run_task(self, last_run, ainjector=self.ainjector)
            if run_task:
                return False  # We're a check_completed function not a should_run function
        return last_run

    async def apply(self):
        ''' Run setup tasks against host'''
        return await self.ainjector(self.run_setup_tasks, context=self.customization_context)

    def __getattr__(self, a):
        if a in ('ssh', 'ip_address', 'start_machine', 'stop_machine',
                 "filesystem_access",
                 'model',
                 'name', 'ansible_inventory_name',
                 'machine_running', 'running',
                 'name', 'full_name',
                 'apply_customization'):
            return getattr(self.host, a)
        raise AttributeError(f"'{self}' has no attribute '{a}'")

    def __repr__(self):
        return f"<{self.__class__.__name__} description:\"{self.description}\" for {self.host.name}>"

    #: A description of the customization for inclusion in task logging
    description = ""


class MachineCustomization(BaseCustomization):

    '''A customization class for running customizations on running machines.'''

    @property
    def customization_context(self):
        return self.host.machine_running()


class ContainerCustomization(BaseCustomization):

    '''A customization class for running tasks on :class:`~carthage.container.Container` instances or :class:`~carthage.image.ImageVolume` instances without actually booting the container.  May also run on objects providing :meth:`_apply_to_container_customization` like :class:`carthage.podman.PodmanContainer`.  This is valuable for tasks used in image production that want to manipulate the filesystem.
'''

    def __init__(self, apply_to, **kwargs):
        if not hasattr(apply_to, '_apply_to_container_customization'):
            raise TypeError(f'{self.__class__.__name__} can only be applied to Containers or ImageVolumes')
        super().__init__(apply_to=apply_to, **kwargs)
        apply_to._apply_to_container_customization(self)

    @memoproperty
    def path(self):
        return self.host.volume.path

    def __getattr__(self, a):
        if a in ('container_command', ):
            return getattr(self.host, a)
        else:
            return super().__getattr__(a)


class FilesystemCustomization(BaseCustomization):

    '''
    A Customization class for interacting with the filesystem either of a :class:`carthage.container.Container`, :class:`ImageVolume` or :class:`Machine`.  If possible (for containers and image volumes), do not actually boot the machine.
'''

    def __init__(self, apply_to, **kwargs):
        super().__init__(apply_to, **kwargs)
        self.host._apply_to_filesystem_customization(self)

    @contextlib.asynccontextmanager
    async def _machine_context(self):
        async with self.host.machine_running(), self.host.filesystem_access() as path:
            self.path = path
            yield
            return


class CustomizationInspectorProxy:

    def __init__(self, obj, stamp):
        self.obj = obj
        self.stamp_stem = stamp


    def check_stamp(self, s, *args):
        return self.obj.check_stamp(self.stamp_stem+'-'+s, *args)
    

    @property
    def logger_for(self):
        return self.obj.logger_for

    @property
    def stamp_path(self):
        return self.obj.stamp_path

    def __repr__(self):
        return f'CustomizationInspectorProxy({repr(self.obj)})'
    
class CustomizationWrapper(TaskWrapperBase):

    customization: typing.Type[BaseCustomization]

    def __init__(self, customization, before=None, **kwargs):
        self.customization = customization
        if before:
            kwargs['order'] = before.order - 1
        try:
            if kwargs['order'] is None:
                del kwargs['order']
        except AttributeError:
            pass
        kwargs['description'] = getattr(customization, 'description', None) or customization.__name__
        super().__init__(**kwargs)

    @memoproperty
    def stamp(self):
        raise RuntimeError('This CustomizationTask was never assigned to a SetupTaskMixin')

    async def func(self, machine):
        await machine.apply_customization(self.customization, stamp=self.stamp)

    async def should_run_task(self, obj, dependency_last_run=0.0, *, ainjector):
        res = await obj.apply_customization(self.customization, method="last_run", stamp=self.stamp)
        # unfortunately if we return a last_run and one of our
        # dependencies has run more recently, we will continue to
        # generate unneeded task runs because we never inject our
        # dependency's last run into the customization so we never
        # update our last_run time.
        if not res: return True, dependency_last_run
        if res > dependency_last_run:
            dependency_last_run = res
        return False, dependency_last_run

    def inspect(self, obj, instance_id=None):
        if instance_id is None: instance_id = id(obj)
        proxy = CustomizationInspectorProxy(obj, self.stamp)
        prev_inspector = None
        for t in self.customization.class_setup_tasks():
            prev_inspector = TaskInspector(task=t, from_obj=proxy, previous=prev_inspector)
            prev_inspector.stamp = self.stamp+'-'+prev_inspector.stamp
            prev_inspector.instance_id = instance_id
            yield prev_inspector
            

def customization_task(c: BaseCustomization, order: int = None,
                       before=None):
    '''
    :return: a setup_task for using a particular :class:`Customization` in a given :class:`Machine`.

    Usage::

        # in a machine
        add_packages = customization_task(AddOurPackagesCustomization)

    '''
    return CustomizationWrapper(customization=c,
                                order=order,
                                before=before)


@inject_autokwargs(ssh_key=InjectionKey(SshKey, _ready=True))
class BareMetalMachine(Machine, SetupTaskMixin):

    '''Represents physical hardware that Carthage cannot start or stop
    '''

    running = False

    async def start_machine(self):
        if self.running:
            return
        await self.start_dependencies()
        await super().start_machine()
        await self.ssh_online()
        self.running = True

    async def stop_machine(self):
        await super().stop_machine()
        self.running = False

    async def async_ready(self):
        await self.resolve_networking()
        await self.run_setup_tasks()
        await super().async_ready()

    async def is_machine_running(self):
        return self.running

    @memoproperty
    def stamp_path(self):
        return Path(f'{self.config_layout.state_dir}/machines/{self.name}')


def disk_config_from_model(model, default_disk_config):
    '''Return a :ref:`disk_config <disk_config>` specification from a model.  Handles *disk_sizes* and makes sure there is always an entry for the primary disk.
'''
    primary_disk_found = False
    if hasattr(model, 'disk_config'):
        for entry in model.disk_config:
            primary_disk_found = True
            yield dict(entry)  # copy the first level of the dict
    elif hasattr(model, 'disk_sizes'):
        for size in model.disk_sizes:
            primary_disk_found = True
            yield dict(size=size)
    if not primary_disk_found:
        yield from default_disk_config


__all__ = ['AbstractMachineModel',
           'ssh_origin',
           'Machine', 'MachineRunning', 'BareMetalMachine',
           'ResolvableModel', 'NetworkedModel',
           'SshMixin', 'BaseCustomization', 'ContainerCustomization',
           'FilesystemCustomization',
           'MachineCustomization']
