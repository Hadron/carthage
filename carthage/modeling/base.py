# Copyright (C) 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, logging, os, types
from .implementation import *
from .decorators import *
from carthage.dependency_injection import * #type: ignore
from carthage.utils import when_needed, memoproperty
from carthage import ConfigLayout
import typing
import carthage.network
import carthage.machine
from .utils import *

logger = logging.getLogger(__name__)

__all__ = []

def dependency_quote_class(c: type):
    "When *c* is injected in a model, dependency_quote the result"
    from .implementation import classes_to_quote
    classes_to_quote.add(c)

__all__ += ['dependency_quote_class']

dependency_quote_class(carthage.machine.BaseCustomization)


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
    def add(cls, ns, interface, *, mac, **kwargs):
        kwargs['mac'] = mac
        def callback(inst):
            nonlocal kwargs
            keys = kwargs.keys()
            values = key_from_injector_access(*kwargs.values())
            kwargs = {k:v for k,v in zip(keys, values)}
            try:
                inst.add(interface, **kwargs)
            except TypeError as e:
                raise TypeError(f'Error constructing {interface} with arguments {kwargs}') from e
        cls._add_callback(ns, callback)

class NetworkConfigModel(InjectableModel,
                         carthage.network.NetworkConfig,
                         metaclass = NetworkConfigModelType
                         ):
    pass

__all__ += ['NetworkConfigModel']

class ModelGroup(InjectableModel, AsyncInjectable, metaclass = ModelingContainer):

    async def all_models(self, ready = None):
        models = await self.ainjector.filter_instantiate_async(
            MachineModel, ['host'],
            stop_at = self.injector,
            ready = ready)
        return [m[1] for m in models]

    async def resolve_networking(self, force = False):
        if hasattr(self, 'resolve_networking_models'):
            return self.resolve_networking_models
        async def await_futures(pending_futures, event, target, **kwargs):
            if pending_futures:
                await asyncio.gather(*pending_futures)
        models = await self.all_models(ready = False)
        with self.injector.event_listener_context(
                InjectionKey(carthage.network.NetworkConfig), "resolved",
                await_futures) as event_futures:
            resolve_networking_futures = []
            for m in models:
                resolve_networking_futures.append(asyncio.ensure_future(m.resolve_networking(force)))
            if resolve_networking_futures: await asyncio.gather( *resolve_networking_futures)
        if event_futures: await asyncio.gather(*event_futures)
        self.resolve_networking_models = models
        return models

    def close(self, canceled_futures = None):
        try: del self.resolved_networking_models
        except: pass
        super().close(canceled_futures)
        
        
    async def generate(self):
        async def cb(m):
            try:
                await m.async_become_ready()
            except Exception:
                logger.exception(f"Error generating for {repr(m)}")
                raise
        models = await self.resolve_networking()
        futures = []
        for m in models:
            if not isinstance(m, AsyncInjectable): continue
            futures.append(asyncio.ensure_future(cb(m)))
        if futures: await asyncio.gather(*futures)
        if hasattr(super(), 'generate'):
            await super().generate()
        
            
    


class Enclave(ModelGroup, metaclass = ModelingContainer):

    domain: str

    @classmethod
    def our_key(self):
        return InjectionKey(Enclave, domain=self.domain)

__all__ += ['ModelGroup', 'Enclave']

machine_implementation_key = InjectionKey(carthage.machine.Machine, role = "implementation")

__all__ += [ 'machine_implementation_key']



class MachineModelType(ModelingContainer):

    classes_to_inject = (carthage.machine.BaseCustomization,)


    @staticmethod
    def calc_mixin_key(class_name, ns, bases):
        # if we had the class instantiated, we'd just look at
        # self.name, and make up a name from self.__name__ if
        # self.name is not set.  We have to manually traverse the
        # bases (and thus the mro) since we need to perform this
        # before the class is instantiated.
        name = None
        if 'name' in ns:
            name = ns['name']
        else:
            for b in bases:
                if hasattr(b, 'name'):
                    name = b.name
                    break
        if not name:
            name = class_name.lower()
        if '.' not in name:
            try:
                if ns['domain']: name += '.'+ns['domain']
            except KeyError: pass
            
        return InjectionKey(MachineModelMixin, host=name)


    def __new__(cls, name, bases, ns, mixin_key = None, **kwargs):
        bases = adjust_bases_for_tasks(bases, ns)
        template = kwargs.get('template', False)
        if not template:
            if mixin_key is None: mixin_key = cls.calc_mixin_key(name, ns, bases)
            try:
                if mixin_key:
                    mixin = ns.get_injected(mixin_key)
                    bases += (mixin,)
            except KeyError: pass
        domain = ns.get('domain', None)
        self =  super().__new__(cls, name, bases, ns, **kwargs)
        if not template:
            if not hasattr(self, 'name'):
                self.name = self.__name__.lower()
            if domain and not '.' in self.name:
                self.name += '.'+domain
        return self


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

class MachineModelMixin: pass

@inject_autokwargs(config_layout = ConfigLayout)
class MachineModel(InjectableModel, carthage.machine.AbstractMachineModel, metaclass = MachineModelType, template = True):

    '''

    Represents the aspects of a :class:`~carthage.machine.Machine` that are independent of the implementation of that machine.  Typically includes things like:

    * Network configuration (:class:`~carthage.network.NetworkConfig`

    * Configuration for devops tools like Ansible

    * Selecting the target implementation (whether the machine will be a VM, container, or hosted on real hardware)

    Applications that want to reason about the abstract environment typically only need to instantiate models.  Applications that want to build VMs or effect changes to real hardware instantiate the machine implementations.  This class is the modeling extensions to :class:`carthage.machine.AbstractMachineModel`.

    If a *MachineModel* contains reference to :func:`setup_tasks <carthage.setup_task.setup_task>`, then it will automatically gain :class:`~carthage.setup_task.SetupTaskMixin` as a base class.  Similarly, if :func:`~carthage.modeling.decorators.model_mixin_for` is used to decorate a class in the same :class:`CarthageLayout` encountered before the *MachineModel*, then that class will be added as an implicit base class of the model.

    Class Parameters passed in as keywords on the class statement:

    :param template: True if this class represents a base class or mixin rather than a actual model of a specific machine.

    :param mixin_key: The :class:`InjectionKey` to use to search for mixins.

    '''
    
    @classmethod
    def our_key(cls):
        "Returns the globally unique InjectionKey by which this model is known."
        return InjectionKey(MachineModel, host = cls.name)



    def __repr__(self):
        return f'<{self.__class__.__name__} model name: {self.name}>'
    
    network_config = injector_access(InjectionKey(carthage.network.NetworkConfig))

    #: A set of ansible groups to add a model to; see :func:`carthage.modeling.ansible.enable_modeling_ansible`.
    ansible_groups: typing.Sequence[str]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.network_links = {}
        self.injector.add_provider(InjectionKey(MachineModel), dependency_quote(self))
        self.injector.add_provider(InjectionKey(carthage.machine.AbstractMachineModel), dependency_quote(self))
        machine_key = InjectionKey(carthage.machine.Machine, host = self.name)
        if machine_key in self.__class__.__initial_injections__: # not transcluded
            self.injector.add_provider(InjectionKey(carthage.machine.Machine), MachineImplementation)
        else:
            self.injector.add_provider(InjectionKey(carthage.machine.Machine), injector_access(machine_key))
            

    machine = injector_access(InjectionKey(carthage.machine.Machine))


    #: Sequence of classes to be mixed into the resulting machine implementation
    machine_mixins = tuple()
    
    @memoproperty
    def machine_type(self):
        implementation = self.injector.get_instance(machine_implementation_key)
        bases = [implementation] + list(map(lambda x: x[1], self.injector.filter_instantiate(MachineMixin, ['name'])))
        bases += self.machine_mixins
        for b in bases:
            assert isinstance(b, type) or hasattr(b, '__mro_entries__'), f'{b} is not a type; did you forget a dependency_quote'
        res =  types.new_class("MachineImplementation", tuple(bases))
        try:
            customization = self.injector.get_instance(carthage.machine.BaseCustomization)
            res.model_customization = carthage.machine.customization_task(customization)
        except KeyError: pass
        res.model = self
        return res

    @memoproperty
    def stamp_path(self):
        path = self.config_layout.output_dir+ f"/hosts/{self.name}"
        os.makedirs(path, exist_ok = True)
        return path

    async def resolve_networking(self, *args, **kwargs):
        '''
        See :meth:`~carthage.machine.AbstractMachineModel.resolve_networking` for documentation.

        In adition to the standard behavior, if  :meth:`machine_type` is an instance of :class:`~carthage.local.LocalMachine`,
then call :func:`carthage.local.process_local_network_config` to learn about local bridges.
        '''
        res = await super().resolve_networking(*args, **kwargs)
        from carthage.local import LocalMachine, process_local_network_config
        try:
            if issubclass(self.machine_type, LocalMachine):
                process_local_network_config(self)
        except KeyError: pass #no machine_implementation_key
        return res

    

@inject(injector = Injector,
            model = MachineModel,
            )
class MachineImplementation(AsyncInjectable):

    # Another class that is only a type because of how the injection
    # machineary works.

    def __new__(cls, injector,  model):
        res = model.machine_type
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
        try: implementation.short_name = model.short_name
        except AttributeError: pass
        return implementation

    async def async_resolve(self):
        return self.prep(await self.ainjector(self.res, name = self.name), self.model)


__all__ += ['MachineModel', 'MachineModelMixin']

class CarthageLayout(ModelGroup):

    @classmethod
    def default_class_injection_key(cls):
        if cls.layout_name:
            return InjectionKey(CarthageLayout, layout_name = cls.layout_name)
        else: return InjectionKey(CarthageLayout)
        

    @property
    def layout_name(self):
        raise NotImplementedError("You must specify a name on a layout")

    __all__ += ['CarthageLayout']
    
@inject(injector = Injector)
def model_bases(host: str, *bases,
                   injector):
    '''

    One common modeling pattern is to automatically generate  :class:`MachineModel`s for a number of systems from some sort of inventory database.  However, it is often desirable to add a override mechanism so that customizations can be added for a specific model.  This function adds a modeling mixin if one is registered in the injector.  In the automated code it is used like::

        @dynamic_name(inventory.name)
        class model( *injector(model_bases,   inventory.fqdn, MachineModel)):
            name = inventory.fqdn

    And then to add an override somewhere in an injector that :func:`transcludes <transclude_injector>` the model::

        @model_mixin_for(host = "foo.com")
        class FooMixin(MachineModel):
            #This will be a base of the foo.com model

    Compare and contrast with using :func:`transclude_overrides` which will replace the overridden model rather than augmenting it with a mixin.

    '''
    new_bases = list(bases)
    try:
        new_base = injector.get_instance(
            InjectionKey(MachineModelMixin, host = host, ))
        if issubclass(new_base, bases):
            new_bases.insert(0, new_base)
        else:
            new_bases.append(new_base)
    except KeyError: pass
    return tuple(new_bases)

__all__ += ['model_bases']

