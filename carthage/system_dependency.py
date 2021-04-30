# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import abc
from .dependency_injection import *
from .machine import Machine

__all__ = []

class SystemDependency(abc.ABC, Injectable):

    '''Represents a dependency that may be required by a :class:`carthage.Machine` before a machine is started.  These dependencies may also be required by a :func:`carthage.setup_tasks.setup_task`; see :func:`depend_on()`.

    Note that typically instances of this class rather than subclasses are used as dependency providers on an Injector.  That way, :meth:`~carthage.Injector.get_instance` returns the instance without processing injected dependencies.  These dependencies are later processed when a method like :meth:`carthage.Machine.start_dependencies()` calls the :meth:`__call__()` method.

    
'''

    name:str
    @abc.abstractproperty
    def name(self): raise NotImplementedError

    @abc.abstractmethod
    def __call__(self, ainjector:AsyncInjector): raise NotImplementedError

    def default_instance_injection_key(self):
        return InjectionKey(SystemDependency, name = self.name)

    @property
    def __globally_unique_key__(self):
        # This will make sure that dependencies in a modeling.InjectableModel are added to the injector so they are found 
        return self.default_instance_injection_key()
    
    def __repr__(self):
        return f'<SystemDependency name={self.name}'
    
__all__ += ['SystemDependency']

class MachineDependency(SystemDependency):

    def __init__(self, m, *,
                 name = None,
                 online ='ssh_online'):
        if isinstance(m, InjectionKey):
            self.key = InjectionKey(m, _ready = True)
        elif isinstance(m, str):
            self.key = InjectionKey(Machine, host = m, _ready = True)
        elif isinstance(m, Machine):
            self.key = InjectionKey(Machine, host = m.name, _ready = True)
        else: raise ValueError
        self.online = online
        if name: self.name = name
        

    async def __call__(self, ainjector):
        machine = await ainjector.get_instance_async(self.key)
        await machine.start_machine()
        if self.online:
            await getattr(machine, self.online)()

    @property
    def name(self):
        return self.key.constraints['host']

    def __repr__(self):
        return f'<MachineDependency for {self.name}>'
    

__all__ += ['MachineDependency']


def disable_system_dependency(injector, dependency):
    "Mask out *dependency* in the scope of *injector*"
    injector.add_provider(dependency.default_instance_injection_key(), None)

__all__ += ['disable_system_dependency']
