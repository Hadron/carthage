# Copyright (C) 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import os, types
from .implementation import *
from .decorators import *
from carthage.dependency_injection import * #type: ignore
from carthage.utils import when_needed, memoproperty
from carthage import ConfigLayout
import typing
import carthage.network
import carthage.machine

from .utils import *
__all__ = []


@inject_autokwargs(injector = Injector)
class InjectableModel(Injectable, metaclass = InjectableModelType):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        injector = self.injector
        dependency_providers: typing.Mapping[typing.Any,DependencyProvider] = {}
        # This is complicated because we want to reuse the same
        # DependencyProvider when registering the same value more than
        # once so that instantiations alias and we don't accidentally
        # get multiple instances of the same type providing related
        # but different keys.
        for k,info in self.__class__.__initial_injections__.items():
            v, options = info
                
            try:
                dp = dependency_providers[v]
            # TypeError: unhashable
            except (KeyError, TypeError):
                dp = DependencyProvider(
                    v, close = options['close'],
                    allow_multiple = options['allow_multiple'])
                try: dependency_providers[v] = dp
                except TypeError: pass
            options = dict(options)
            try: del options['globally_unique']
            except: pass
            try:
                self.injector.add_provider(k, dp, replace = True, **options)
            except Exception as e:
                raise RuntimeError(f'Failed registering {v} as provider for {k}') from e

        for c in reversed(self.__class__.__mro__):
            if isinstance(c, ModelingBase) and hasattr(c, '_callbacks'):
                for cb in c._callbacks:
                    cb(self)

    def __init_subclass__(cls, *args, template = False, **kwargs):
        super().__init_subclass__(*args, **kwargs)

__all__ += ['InjectableModel']

class NetworkModel(carthage.Network, InjectableModel, metaclass = ModelingContainer):

    def __init__(self, **kwargs):
        kwargs.update(gather_from_class(self, 'name', 'vlan_id'))
        super().__init__(**kwargs)
        if hasattr(self,'bridge_name'):
            self.ainjector.add_provider(InjectionKey(carthage.network.BridgeNetwork),
                                        when_needed(carthage.network.BridgeNetwork, bridge_name = self.bridge_name, delete_bridge = False))

__all__ += ['NetworkModel']

class NetworkConfigModelType(InjectableModelType):

    @modelmethod
    def add(cls, ns, interface, net, mac):
        def callback(inst):
            nonlocal mac, net
            mac, net = key_from_injector_access(mac, net)
            inst.add(interface, net, mac)
        cls._add_callback(ns, callback)

class NetworkConfigModel(InjectableModel,
                         carthage.network.NetworkConfig,
                         metaclass = NetworkConfigModelType
                         ):
    pass


__all__ += ['NetworkConfigModel']

class ModelGroup(InjectableModel, metaclass = ModelingContainer): pass

class Enclave(InjectableModel, metaclass = ModelingContainer):

    domain: str

    @classmethod
    def our_key(self):
        return InjectionKey(Enclave, domain=self.domain)

__all__ += ['ModelGroup', 'Enclave']

machine_implementation_key = InjectionKey(carthage.machine.Machine, role = "implementation")

__all__ += [ 'machine_implementation_key']



class MachineModelType(ModelingContainer):

    def __new__(cls, name, bases, ns, **kwargs):
        if 'name' not in ns:
            ns['name'] = name.lower()
        if '.' not in ns['name']:
            try:
                ns['name'] = ns['name'] + '.' + ns['domain']
            except KeyError: pass
        bases = adjust_bases_for_tasks(bases, ns)
        return super().__new__(cls, name, bases, ns, **kwargs)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not kwargs.get('template', False):
            self.__globally_unique_key__ = self.our_key()
            machine_key = InjectionKey(carthage.machine.Machine, host = self.name)
            self.__transclusions__ |= {
                (machine_key, machine_key, self),
                (self.our_key(), machine_key, self),
                }
            self.__initial_injections__[machine_key] = (
                    self.machine, dict(
                        close = True, allow_multiple = False,
                        globally_unique = True))
            self.__container_propagations__[machine_key] = \
                self.__initial_injections__[machine_key]






@inject_autokwargs(config_layout = ConfigLayout)
class MachineModel(InjectableModel, carthage.machine.AbstractMachineModel, metaclass = MachineModelType, template = True):

    @classmethod
    def our_key(cls):
        return InjectionKey(MachineModel, host = cls.name)

    network_config = injector_access(InjectionKey(carthage.network.NetworkConfig))

    #: A set of ansible groups to add a model to; see :func:`carthage.modeling.ansible.enable_modeling_ansible`.
    ansible_groups: typing.Sequence[str]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.injector.add_provider(InjectionKey(MachineModel), dependency_quote(self))
        machine_key = InjectionKey(carthage.machine.Machine, host = self.name)
        if machine_key in self.__class__.__initial_injections__: # not transcluded
            self.injector.add_provider(InjectionKey(carthage.machine.Machine), MachineImplementation)
        else:
            self.injector.add_provider(InjectionKey(carthage.machine.Machine), injector_access(machine_key))
            

    machine = injector_access(InjectionKey(carthage.machine.Machine))

    @memoproperty
    def stamp_path(self):
        path = self.config_layout.output_dir+ f"/hosts/{self.name}"
        os.makedirs(path, exist_ok = True)
        return path
    

@inject(injector = Injector,
            implementation = machine_implementation_key,
            model = MachineModel,
            )
class MachineImplementation(AsyncInjectable):

    # Another class that is only a type because of how the injection
    # machineary works.

    def __new__(cls, injector, implementation, model):
        bases = [implementation] + list(map(lambda x: x[1], injector.filter_instantiate(MachineMixin, ['name'])))
        for b in bases:
            assert isinstance(b, type) or hasattr(b, '__mro_entries__'), f'{b} is not a type; did you forget a dependency_quote'
            res = types.new_class("MachineImplementation", tuple(bases), {})
        try:
            return cls.prep(injector(res, name = model.name), model)
        except AsyncRequired:
            self = super().__new__(cls)
            self.name = model.name
            self.model = model
            self.injector = injector
            self.res = res
            return self

    @staticmethod
    def prep(implementation: carthage.machine.Machine, model: MachineModel):
        implementation.model = model
        try: implementation.ip_address = model.ip_address
        except AttributeError: pass
        try: implementation.short_name = model.short_name
        except AttributeError: pass
        return implementation

    async def async_resolve(self):
        return self.prep(await self.ainjector(self.res, name = self.name), self.model)


__all__ += ['MachineModel']
