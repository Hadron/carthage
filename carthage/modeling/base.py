# Copyright (C) 2019, 2020, 2021, 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import logging
import os
import types
import typing
from pathlib import Path
from .implementation import *
from .decorators import *
from carthage.dependency_injection import *  # type: ignore
from carthage.utils import when_needed, memoproperty
from carthage import ConfigLayout, SetupTaskMixin
import carthage.kvstore
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


@inject_autokwargs(injector=Injector)
class InjectableModel(Injectable, metaclass=InjectableModelType):

    def __init__(self, *args, _not_transcluded=None, **kwargs):
        self.__class__._already_instantiated = True # no more calling modelmethods
        super().__init__(*args, **kwargs)
        injector = self.injector
        dependency_providers: typing.Mapping[typing.Any, DependencyProvider] = {}
        ignored_keys = set()
        parent_injector = injector.parent_injector
        not_transcluded = set()
        if _not_transcluded: not_transcluded.update(_not_transcluded)
        for k, to_ignore in self.__transclusions__.items():
            if k in not_transcluded: continue
            target = parent_injector.injector_containing(k)
            if target:
                ignored_keys |= to_ignore
                # For each alias that a transcluded item may be known by, set up an injector_xref back to the base transcluded key
                for alias in to_ignore:
                    if alias is k: continue # It's the primary key
                    try: injector.add_provider(alias, injector_xref(None, k))
                    except ExistingProvider: pass
            else: not_transcluded |= to_ignore
        if not_transcluded: self.injector.add_provider(not_transcluded_key, not_transcluded)
        self.ignored_by_transclusion = frozenset(ignored_keys)
        # This is complicated because we want to reuse the same
        # DependencyProvider when registering the same value more than
        # once so that instantiations alias and we don't accidentally
        # get multiple instances of the same type providing related
        # but different keys.
        for k, info in self.__class__.__initial_injections__.items():
            v, options = info
            if k in ignored_keys: continue
            try:
                dp = dependency_providers[v]
            # TypeError: unhashable
            except (KeyError, TypeError):
                dp = DependencyProvider(
                    v, close=options['close'],
                    allow_multiple=options['allow_multiple'])
                try:
                    dependency_providers[v] = dp
                except TypeError:
                    pass
            options = dict(options)
            try:
                self.injector.add_provider(k, dp, replace=True, **options)
            except Exception as e:
                raise RuntimeError(f'Failed registering {v} as provider for {k}') from e

        for c in reversed(self.__class__.__mro__):
            if isinstance(c, ModelingBase) and hasattr(c, '_callbacks'):
                for cb in c._callbacks:
                    cb(self)

    def __init_subclass__(cls, *args, template=False, **kwargs):
        super().__init_subclass__(*args, **kwargs)

    def __str__(self):
        keys = []
        for a in ('__container_propagation_keys__', '__provides_dependencies_for__'):
            res = getattr(self, a, None)
            if res:
                keys = res
                break
        if not keys: return super().__str__()
        keys = list(keys)
        # We want globally_unique is True to sort before
        # globally_unique False and then most constraints first.
        # So that's reversed of globally_unique followed by len(constraints)
        keys.sort(key=lambda k: (k.globally_unique, len(k.constraints)), reverse=True)
        result_key = keys[0]
        if isinstance(result_key.target, type):
            result = [result_key.target.__name__]
        else: result = [str(result_key.target)]
        result += [f"{k}={v}" for k,v in result_key.constraints.items()]
        if len(result) > 1:
            return result[0]+":"+(" ".join(result[1:]))
        else: return result[0]
        
    
        

__all__ += ['InjectableModel']

class ModelContainer(InjectableModel, metaclass=ModelingContainer): pass

__all__ +=  ['ModelContainer']





class NetworkModel(carthage.Network, ModelContainer):

    def __init__(self, **kwargs):
        kwargs.update(gather_from_class(self, 'name', 'vlan_id'))
        super().__init__(**kwargs)
        if hasattr(self, 'bridge_name'):
            self.ainjector.add_provider(InjectionKey(carthage.network.BridgeNetwork),
                                        when_needed(carthage.network.BridgeNetwork, bridge_name=self.bridge_name, delete_bridge=False))


__all__ += ['NetworkModel']


class NetworkConfigModelType(InjectableModelType):

    @modelmethod
    def add(cls,  interface, *, mac, **kwargs):
        kwargs['mac'] = mac
        if 'net' not in kwargs:
            raise SyntaxError('net is required')
        if isinstance(kwargs['net'], type):
            # see if we can construct an appropriate injector_access
            net = kwargs['net']
            if issubclass(net, NetworkModel) and hasattr(net, '__provides_dependencies_for__'):
                kwargs['net'] = injector_access(net.__provides_dependencies_for__[0])
            else:
                raise SyntaxError(
                    f'net must be an instance of Network (or InjectionKey) not a {kwargs["net"]}; consider wrapping in injector_access')

        def callback(inst):
            nonlocal kwargs
            keys = kwargs.keys()
            values = key_from_injector_access(*kwargs.values())
            kwargs = {k: v for k, v in zip(keys, values)}
            try:
                inst.add(interface, **kwargs)
            except TypeError as e:
                raise TypeError(f'Error constructing {interface} with arguments {kwargs}') from e
        cls._add_callback(callback)


class NetworkConfigModel(InjectableModel,
                         carthage.network.NetworkConfig,
                         metaclass=NetworkConfigModelType
                         ):
    pass


__all__ += ['NetworkConfigModel']


class ModelGroup(ModelContainer, AsyncInjectable):

    async def all_models(self, ready=None):
        models = await self.ainjector.filter_instantiate_async(
            carthage.machine.ResolvableModel, ['name'],
            stop_at=self.injector,
            ready=ready)
        return [m[1] for m in models]

    async def resolve_networking(self, force=False):
        if hasattr(self, 'resolve_networking_models') and not force:
            return self.resolve_networking_models

        async def await_futures(pending_futures, event, target, **kwargs):
            if pending_futures:
                await asyncio.gather(*pending_futures)
        if not hasattr(self, 'all_model_tasks'):
            model_tasks = await self.ainjector.filter_instantiate_async(
                ModelTasks, ['name'],
                ready=False)
            self.all_model_tasks = [m[1] for m in model_tasks]
        models = await self.all_models(ready=False)
        with self.injector.event_listener_context(
                InjectionKey(carthage.network.NetworkConfig), "resolved",
                await_futures) as event_futures:
            resolve_model_futures = []
            for m in models:
                resolve_model_futures.append(asyncio.ensure_future(m.resolve_model(force)))
            if resolve_model_futures:
                await asyncio.gather(*resolve_model_futures)
        if event_futures:
            await asyncio.gather(*event_futures)
        self.resolve_networking_models = models
        return models

    def close(self, canceled_futures=None):
        try:
            del self.resolved_networking_models
        except BaseException:
            pass
        super().close(canceled_futures)

    async def generate(self):
        async def cb(m):
            try:
                await m.async_become_ready()
            except Exception:
                logger.exception(f"Error generating for {repr(m)}")
                raise
        models = await self.resolve_networking()
        models += self.all_model_tasks
        futures = []
        for m in models:
            if not isinstance(m, AsyncInjectable):
                continue
            futures.append(asyncio.ensure_future(cb(m)))
        if futures:
            await asyncio.gather(*futures)
        if hasattr(super(), 'generate'):
            await super().generate()

    async def async_ready(self):
        await self.resolve_networking()
        return await super().async_ready()


class Enclave(ModelGroup):

    domain: str

    @classmethod
    def our_key(self):
        return InjectionKey(Enclave, domain=self.domain)


__all__ += ['ModelGroup', 'Enclave']

machine_implementation_key = InjectionKey(carthage.machine.Machine, role="implementation")

__all__ += ['machine_implementation_key']

dependency_quote_class(carthage.machine.BaseCustomization)


class RoleType(ModelingContainer):

    classes_to_inject = (carthage.machine.BaseCustomization,)

class MachineModelType(RoleType):

    namespace_filters = [_handle_base_customization]

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
                if ns['domain']:
                    name += '.' + ns['domain']
            except KeyError:
                pass

        return InjectionKey(MachineModelMixin, host=name)

    def __new__(cls, name, bases, ns, mixin_key=None, **kwargs):
        bases = adjust_bases_for_tasks(bases, ns)
        template = kwargs.get('template', False)
        if not template:
            if mixin_key is None:
                mixin_key = cls.calc_mixin_key(name, ns, bases)
            try:
                if mixin_key:
                    mixin = ns.get_injected(mixin_key)
                    bases += (mixin,)
            except KeyError:
                pass
        domain = ns.get('domain', None)
        self = super().__new__(cls, name, bases, ns, **kwargs)
        if not template:
            if not hasattr(self, 'name'):
                self.name = self.__name__.lower()
            if domain and not '.' in self.name:
                self.name += '.' + domain
        return self

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not kwargs.get('template', False):
            machine_key = InjectionKey(carthage.machine.Machine, host=self.name, _globally_unique=True)
            self.__transclusions__[machine_key] = frozenset({machine_key})
            self.__transclusion_key__ = self.our_key()
            self.__initial_injections__[machine_key] = (
                self.machine, dict(
                    close=True, allow_multiple=False,
                ))
            self.__container_propagations__.add(machine_key)
            propagate_key(InjectionKey(
                carthage.machine.ResolvableModel,
                name=self.name, _globally_unique=True), self)
            propagate_key(InjectionKey(MachineModel, host=self.name, _globally_unique=True), self)


class MachineModelMixin:
    pass


@inject(
    _not_transcluded=not_transcluded_key)
@inject_autokwargs(
    config_layout=ConfigLayout,
                   )
class MachineModel(ModelContainer, carthage.machine.AbstractMachineModel, metaclass=MachineModelType, template=True):

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


    Any :class:`carthage.network.NetworkConfig` present in the *MachineModel* will be used as the network configuration.

Every :class:`carthage.machine.BaseCustomization` (including MachineCustomization, FilesystemCustomization and ContainerCustomization) will be integrated into the resulting machine:

    * If the customization is called *cust*, then a method will be added to the machine *cust_task* which is a :func:`carthage.machine.customization_task` to run the customization.

    * On the model, *cust*  will become a  method that will produce an instance of the customization applied to the machine.

    For example::

        class server(MachineModel):

            class install_software(MachineCustomization):

                webserver_role = ansible_role_task('webserver')

                database_role = ansible_role_task('database')


    Then *server.machine* will have a method *install_software_task* which will run both ansible roles assuming they have not already been run.  *model.install_software* will produce an instance of the customization applied to *model.machine*.  *model.install_software.database_role()* is a method that will force the database_role to run even if it appears up to date.

    '''

    @classmethod
    def our_key(cls):
        "Returns the globally unique InjectionKey by which this model is known."
        return InjectionKey(MachineModel, host=cls.name, _globally_unique=True)

    def __repr__(self):
        return f'<{self.__class__.__name__} model name: {self.name}>'

    @classmethod
    def supplementary_injection_keys(cls, k):
        '''Exclude :class:`AbstractMachineModel` and
        :class:`MachineModel` without constraints from the set of keys
        that are registered.  These keys are searched to find the
        current MachineModel, and if they are implicitly set it can
        produce cases where a MachineModel is accidentally available.  Also, it makes it difficult to have one MachineModel contained within another.
        '''
        excluded = {InjectionKey(MachineModel), InjectionKey(carthage.machine.AbstractMachineModel)}
        for key in super().supplementary_injection_keys(k):
            if key in excluded: continue
            yield key
            

    network_config = injector_access(InjectionKey(carthage.network.NetworkConfig))

    #: A set of ansible groups to add a model to; see :func:`carthage.modeling.ansible.enable_modeling_ansible`.
    ansible_groups: typing.Sequence[str]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.network_links = {}
        self.injector.add_provider(InjectionKey(MachineModel), dependency_quote(self))
        self.injector.add_provider(InjectionKey(carthage.machine.AbstractMachineModel), dependency_quote(self))
        machine_key = InjectionKey(carthage.machine.Machine, host=self.name)
        target = self.injector.injector_containing(machine_key)
        if target is self.injector:
            try:
                self.injector.add_provider(InjectionKey(carthage.machine.Machine), MachineImplementation)
            except ExistingProvider:
                raise SyntaxError('carthage.machine.Machine already registered; typically this means a missing dependency_quote when setting machine_implementation_key')
        else:
            self.injector.add_provider(InjectionKey(carthage.machine.Machine), injector_access(machine_key))

    machine = injector_access(InjectionKey(carthage.machine.Machine))

    #: Sequence of classes to be mixed into the resulting machine implementation
    machine_mixins = tuple()

    @memoproperty
    def machine_type(self):
        try:
            implementation = self.injector.get_instance(machine_implementation_key)
        except AsyncRequired:
            raise AsyncRequired(
                'A provider registered for machine_implementation_key has asynchronous dependencies; did you forget a dependency_quote()')
        bases = [implementation] + list(map(lambda x: x[1], self.injector.filter_instantiate(MachineMixin, ['name'])))
        bases += self.machine_mixins
        for b in bases:
            assert isinstance(
                b, type) or hasattr(
                b, '__mro_entries__'), f'{b} is not a type; did you forget a dependency_quote'
        try: res = types.new_class(implementation.__qualname__, tuple(bases))
        except TypeError as e:
            raise TypeError(f'Unable to create machine_type for {self.name} with bases {bases}: {str(e)}') from None
        inject()(res)  # Pick up any injections from extra bases
        for k, customization in self.injector.filter_instantiate(carthage.machine.BaseCustomization, [
                                                                 'description'], stop_at=self.injector):
            name = customization.__name__
            task = carthage.machine.customization_task(customization)
            setattr(res, f'{name}_task', task)
            task.__set_name__(res, name)

        res.model = self
        return res

    @memoproperty
    def stamp_path(self):
        path = self.config_layout.output_dir + f"/hosts/{self.name}"
        os.makedirs(path, exist_ok=True)
        return Path(path)

    async def resolve_networking(self, *args, **kwargs):
        '''
        See :meth:`~carthage.machine.AbstractMachineModel.resolve_networking` for documentation.

        In adition to the standard behavior, if  :meth:`machine_type` is an instance of :class:`~carthage.local.LocalMachineMixin`,
then call :func:`carthage.local.process_local_network_config` to learn about local bridges.
        '''
        res = await super().resolve_networking(*args, **kwargs)
        from carthage.local import LocalMachineMixin, process_local_network_config, LocalMachine
        try:
            if issubclass(self.machine_type, LocalMachineMixin):
                process_local_network_config(self)
        except KeyError:
            pass  # no machine_implementation_key
        return res


@inject(injector=Injector,
        model=MachineModel,
        )
class MachineImplementation(AsyncInjectable):

    # Another class that is only a type because of how the injection
    # machineary works.

    def __new__(cls, injector, model):
        res = model.machine_type
        try:
            return cls.prep(injector(res, name=model.name), model)
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
        try:
            implementation.short_name = model.short_name
        except AttributeError:
            pass
        return implementation

    async def async_resolve(self):
        return self.prep(await self.ainjector(self.res, name=self.name), self.model)


__all__ += ['MachineModel', 'MachineModelMixin']

class ImageRole(ModelContainer, metaclass=RoleType): pass

__all__ += ['ImageRole']


class CarthageLayout(ModelGroup):

    '''
    *CarthageLayout* is typically the top level class in a set of Carthage models.  It is a :class:`ModelGroup` that represents a complete collection of objects modeled by Carthage.  The primary purpose of this class is to signify the top of a collection of objects; the layout is generally the place to start when examining a collection of models.

    However, a layout differs from a *ModelGroup* in two ways:

    #. :ref:`carthage-runner <carthage_runner>` looks for a :class:`CarthageLayout` to instantiate after loading plugins.  If the console is used, the layout is made available in the *layout* local variable of the console.  If a command is run, the command is run in the context of the layout.

    #. Layouts that set the `carthage.kvstore.persistent_seed_path` in the context of their :class:`Injector` will have persistent assignments of things like IP addresses and MAC addresses loaded from the seed path when instantiated.

    '''


    @classmethod
    def default_class_injection_key(cls):
        if cls.layout_name:
            return InjectionKey(CarthageLayout, layout_name=cls.layout_name)
        else:
            return InjectionKey(CarthageLayout)

    layout_name = None

    def __init__(self,  **kwargs):
        super().__init__(**kwargs)
        self.injector.add_provider(InjectionKey(CarthageLayout), dependency_quote(self))
        persistent_seed_path = self.injector.get_instance(InjectionKey(carthage.kvstore.persistent_seed_path, _optional=True))
        if persistent_seed_path:
            seed_path = Path(persistent_seed_path)
            if seed_path.exists():
                kvstore = self.injector.get_instance(carthage.kvstore.KvStore)
                if not kvstore.persistent_seed_path:
                    kvstore.load(str(seed_path))


__all__ += ['CarthageLayout']


@inject(ainjector=AsyncInjector)
async def instantiate_layout(layout_name=None, *, ainjector, optional=False):
    if layout_name:
        layout = await ainjector.get_instance_async(InjectionKey(CarthageLayout, layout_name=layout_name, _optional=optional))
    else:
        layout = await ainjector.get_instance_async(InjectionKey(CarthageLayout, _optional=optional))
    return layout

__all__ += ['instantiate_layout']


@inject(injector=Injector)
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
            InjectionKey(MachineModelMixin, host=host, ))
        if issubclass(new_base, bases):
            new_bases.insert(0, new_base)
        else:
            new_bases.append(new_base)
    except KeyError:
        pass
    return tuple(new_bases)


__all__ += ['model_bases']


@inject(config_layout=ConfigLayout)
class ModelTasks(ModelContainer, SetupTaskMixin, AsyncInjectable):

    '''
    A grouping of tasks that will be run at generate time in a :class:`CarthageLayout`.  As part of :meth:`ModelGroup.generate`, the layout searches for any :class:`ModelTasks` provided by its injector and instantiates them.  This causes any setup_tasks to be run.

    All :class:`ModelTasks` have a name, which forms part of their key.  If there needs to be an ordering between tasks, the tasks can inject a dependency on other ModelTasks.

    Example usage::

        class layout(CarthageLayout):

            class mt1(ModelTasks):

                @setup_task("some task")
                def some_task(self): # do stuff

    The *async_ready* method will only be called during generate.  However :class:`ModelTasks` will be instantiated whenever at least :meth:`resolve_networking` is called.

    '''

    @classmethod
    def our_key(cls):
        name = getattr(cls, 'name', cls.__name__)
        return InjectionKey(ModelTasks, name=name)

    @memoproperty
    def stamp_path(self):
        name = getattr(self.__class__, 'name', self.__class__.__name__)
        return Path(self.config_layout.output_dir) / name


__all__ += ['ModelTasks']
