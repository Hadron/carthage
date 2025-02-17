# Copyright (C) 2021, 2022, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import abc
import asyncio
from .dependency_injection import *
from .machine import Machine
from .utils import memoproperty
from . import sh
import logging

logger = logging.getLogger("carthage")

__all__ = []


class SystemDependency(abc.ABC, Injectable):

    '''Represents a dependency that may be required by a :class:`carthage.Machine` before a machine is started.  These dependencies may also be required by a :func:`carthage.setup_tasks.setup_task`; see :meth:`carthage.setup_tasks.TaskWrapperBase.depend_on()`.

    Note that typically instances of this class rather than subclasses are used as dependency providers on an Injector.  That way, :meth:`~carthage.Injector.get_instance` returns the instance without processing injected dependencies.  These dependencies are later processed when a method like :meth:`carthage.Machine.start_dependencies()` calls the :meth:`__call__()` method.


'''

    name: str
    @abc.abstractproperty
    def name(self): raise NotImplementedError

    @abc.abstractmethod
    def __call__(self, ainjector: AsyncInjector): raise NotImplementedError

    def default_instance_injection_key(self):
        return InjectionKey(SystemDependency, name=self.name, _globally_unique=True)

    @memoproperty
    def __provides_dependencies_for__(self):
        # This will make sure that dependencies in a modeling.InjectableModel are
        # added to the injector so they are found
        return [self.default_instance_injection_key()]

    def __repr__(self):
        return f'<SystemDependency name={self.name}'


__all__ += ['SystemDependency']


class MachineDependency(SystemDependency):

    ONLINE_DEFAULT = object()

    def __init__(self, m, *,
                 name=None,
                 online=ONLINE_DEFAULT):
        if isinstance(m, InjectionKey):
            self.key = InjectionKey(m, _ready=True)
        elif isinstance(m, str):
            self.key = InjectionKey(Machine, host=m, _ready=True)
        elif isinstance(m, Machine):
            self.key = InjectionKey(Machine, host=m.name, _ready=True)
        else:
            raise ValueError(f'unsupported value for MachineDependency: {m}')
        self.online = online
        if name:
            self._name = name

        if not name and ('host' not in self.key.constraints):
            raise ValueError(f'when MachineDependency is called with an InjectionKey, either the key must have a `host` constraint or a name must be given explicitly')
        self.machine = None
        
    async def __call__(self, ainjector):
        machine = await ainjector.get_instance_async(self.key)
        if not hasattr(machine, 'start_machine') and hasattr(machine, 'machine'):
            # Allow a dependency to be set on a MachineModel not just a machine
            ainjector = machine.injector(AsyncInjector)
            machine = await ainjector.get_instance_async(InjectionKey(Machine, _ready=True))
        await machine.start_machine()
        if self.online is MachineDependency.ONLINE_DEFAULT:
            if machine.machine_running_ssh_online:
                await machine.ssh_online()
        elif self.online:
            await getattr(machine, self.online)()
        self.machine = machine

    @property
    def name(self):
        if hasattr(self, '_name'):
            return self._name
        return self.key.constraints['host']

    def __repr__(self):
        return f'<MachineDependency for {self.name}>'


__all__ += ['MachineDependency']


class CommandDependency(MachineDependency):

    '''
    A kind of :class:`MachineDependency` that repeatedly runs a command until it succeeds after the machine is online.
    '''

    def __init__(self, *args,
                 command:list[str],
                 **kwargs):
        if not isinstance(command, (list, tuple)):
            raise TypeError('Command should be a list of arguments suitable for run_command')
        self.command = command
        super().__init__(*args, **kwargs)

    async def __call__(self, ainjector):
        await super().__call__(ainjector)
        wait = 1
        tries = 0
        while True:
            try:
                await self.machine.run_command(self.command)
                break
            except  sh.ErrorReturnCode:
                tries += 1
                if tries == 3:
                    logger.info('Waiting for %s to succeed on %s', ' '.join(self.command), self.machine.name)
                await asyncio.sleep(wait)
                if wait < 16: wait = wait*2

__all__ += ['CommandDependency']

def disable_system_dependency(injector, dependency):
    "Mask out *dependency* in the scope of *injector*"
    injector.add_provider(dependency.default_instance_injection_key(), None)


__all__ += ['disable_system_dependency']
