# Copyright (C) 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import asyncio
import contextlib
import dataclasses
import enum
import logging
import typing
from .dependency_injection import *
from .dependency_injection import introspection as dependency_introspection, is_obj_ready

logger = logging.getLogger('carthage.deployment')

__all__ = []

class DeletionPolicy(enum.Enum):

    '''
    A policy for how to handle  object removals. There are typically two policies:

    * *destroy_policy*: what to do with a :class:`Deployment` when a layout is destroyed

    * *orphan_policy*: What to do with a model that used to be created by a given layout but is no longer contained in that layout.

    By default *destroy_policy* is *DELETE* and *orphan_policy* is *RETAIN*.
    '''

    #: Retain the deployable without  any diagnostic.
    RETAIN = enum.auto()

    #: Warn about the deployable
    WARN = enum.auto()

    #: Delete the object using default recursion rules for that object.
    DELETE = enum.auto()

__all__ += ['DeletionPolicy']

#: An injection key representing the policy to be applied to objects
#created by a layout when that layout is destroyed.
destroy_policy = InjectionKey(DeletionPolicy, policy='destroy')

__all__ += ['destroy_policy']


#: An injection key describing the policy for how to handle orphaned
#deployables--deployables that were created by the layout but are no
#longer included in it.
orphan_policy = InjectionKey(DeletionPolicy, policy='orphan')

__all__ += ['orphan_policy']

class DryRunType:

    '''A singleton indicating that readonly has been set on an object
    because it was part of the return from
    ``find_deployments(readonly=True)``, which typically means it was
    part of a ``run_deployment(dry_run=True)``.  A future deployment
    run with such an object included in the *deployables* parameter
    will clear *DryRun* as a readonly value, while letting other True
    readonly values remain.
    '''

    def __new__(cls):
        return DryRun

    def __bool__(self):
        return True

    def __repr__(self):
        return 'DryRun'

DryRun = object.__new__(DryRunType)

__all__ += ['DryRun']

    
@dataclasses.dataclass(frozen=True)
class DeploymentFailure:

    '''Represents an object that failed to instantiate during deployment or that raised an error while calling the deployment method.
    If *failing_dependency* is not None, then it is the InjectionKey of a dependency that failed to instantiate. In that case, *exception* is relative to the failing dependency.

    Otherwise *exeception* is an exception raised handling *deployable*.

    Note that :class:`FailedDeployment` is the exception that is
    raised when a deployment fails.  This class is contained in the
    *failures* and *dependency_failures* property of a
    :class:`DeploymentResult`.

    '''
    
    #: In dependency_failures, may be a Deployable class rather than a Deployable instance.
    deployable: typing.Union[Deployable, typing.Type[Deployable]]
    exception: Exception
    dependency_path: typing.Sequence[InjectionKey] = None

__all__ += ['DeploymentFailure']


@dataclasses.dataclass
class DeploymentResult:

    successes: list[Deployable] = dataclasses.field(default_factory=lambda: [])
    failures: list[DeploymentFailure] = dataclasses.field(default_factory=lambda: [])
    dependency_failures: list[DeploymentFailure] = dataclasses.field(default_factory=lambda: [])

    #: A Deployable can be not found either if it is readonly=True or
    #not found on a delete
    not_found: list[Deployable] = dataclasses.field(default_factory=lambda: [])
        
#: Lists instantiation failures that are leaves (not failing because a
    #dependency failed). If the object being instantiated can be
    #identified as a Deployable, it will also appear in *failures*.
    instantiation_failure_leaves: dict[InjectionKey, dependency_introspection.FailedInstantiation] = \
        dataclasses.field(repr=False, default_factory=lambda: {})

    def is_successful(self):
        return self.successes and  not (self.failures or self.dependency_failures or self.instantiation_failure_leaves)

    def __contains__(self, d:Deployable):
        if d in self.successes:
            return True
        if any(x.deployable is d for x in self.failures):
            return True
        if any(x.deployable is d for x in self.dependency_failures):
            return True
        return False
    
    def _injection_failed_cb(self, target, target_key, context, **kwargs):
        try:
            obj = context.get_value_no_instantiate()
        except AttributeError: obj = None
        if hasattr(obj, 'find') and hasattr(obj, 'find_or_create'):
            deployable = obj
            deployment_failure = DeploymentFailure(
                deployable=deployable,
                exception=target.exception,
                # Turn tuple() into None
                dependency_path=target.dependency if target.dependency else None)
        else:
            deployable = None
            deployment_failure = None
        if not target.dependency:
            # We take the first one if there are duplicate InjectionKeys
            self.instantiation_failure_leaves.setdefault(target_key, target)
            if deployment_failure and deployable not in self:
                self.failures.append(deployment_failure)
        else: # there is a dependency_path
            if deployment_failure and deployable not in self:
                self.dependency_failures.append(deployment_failure)

    def method_callback(self, deployable:Deployable):
        def callback(future):
            if  deployable in self:
                # We already noticed an instantiation failure
                if not future.exception():
                    logger.warning("successful method callback for %s, already recorded as a failure in the DeploymentResult", deployable)
                    return
            if exception := future.exception():
                if isinstance(exception, InjectionFailed) and exception.dependency_path:
                    self.dependency_failures.append(DeploymentFailure(
                        deployable, exception=exception.__context__,
                        dependency_path=exception.dependency_path))
                else:
                    self.failures.append(DeploymentFailure(
                        deployable=deployable,
                        exception=exception))
            else: # no exception
                future.result() # so it is read
                self.successes.append(deployable)

        return callback

    def find_callback(self, deployable: Deployable):
        def callback(future):
            if  deployable in self:
                # We already noticed an instantiation failure
                if not future.exception():
                    logger.warning("successful method callback for %s, already recorded as a failure in the DeploymentResult", deployable)
                    return
            if exception := future.exception():
                if isinstance(exception, InjectionFailed) and exception.dependency_path:
                    self.dependency_failures.append(DeploymentFailure(
                        deployable, exception=exception.__cause__,
                        dependency_path=exception.dependency_path))
                else:
                    self.failures.append(DeploymentFailure(
                        deployable=deployable,
                        exception=exception))
            else: # no exception
                if future.result():
                    self.successes.append(deployable)
                else:
                    self.not_found.append(deployable)

        return callback

__all__ += ['DeploymentResult']

class DeploymentIntrospection(dependency_introspection.BaseInstantiationContext):

    '''
    Groups all the dependency operations in a deployment together for instantiation_roots'''

    def __init__(self, injector, method, result:DeploymentResult):
        super().__init__(injector=injector)
        self.method = method
        self.result = result
        self.exit_stack = contextlib.ExitStack()

    def __enter__(self):
        res = super().__enter__()
        self.exit_stack.__enter__()
        self.exit_stack.enter_context(self.injector.event_listener_context(
            InjectionKey(Injector), ['dependency_instantiation_failed'],
            self.result._injection_failed_cb))
        return res

    def __exit__(self, *args):
        try:
            super().__exit__(*args)
            self.done()
            return False
        finally:
            # we always raise the exception so we ignore the return
            # value from the exit stack
            self.exit_stack.__exit__(*args)
            

    @property
    def description(self):
        return f'DEPLOYMENT.{self.method}'

__all__ += ['DeploymentIntrospection']

@typing.runtime_checkable
class Deployable(typing.Protocol):


    '''An object that can be deployed either by calling :meth:`find_or_create` or :meth:`async_become_ready`.

    While the object may not be fully deployed when :meth:`find_or_create` returns, it should be fully deployed when :meth:`async_ready` returns for the first time.
    '''

    async def find_or_create(self):
        '''Find or create this deployable. If this succeeds the deployable exists, but may not be fully deployed.
        '''
        raise NotImplementedError

    async def find(self):
        '''Find whether object exists.
        If this returns True, the object must exist.  If this returns falsy, then if *mob* is set on the object,  it exists.  There is a bit of divergence in how :meth:`find` returns.  See :func:`find_deployable` for a more uniform way to call this function.
        Should not raise simply because the object does not exist.
        '''
        raise NotImplementedError


    #: If True,do not deploy the object bringing it to Ready; used for
    # examining existing objects without creating new objects.
    # Instantiating an object readonly does not stop methods like
    # :meth:`delete` from changing the state of the object.  It only
    # affects what the instantiation process does.
    readonly: typing.Union[bool, DryRun]

    async def dynamic_dependencies(self):
        '''
        Returns a iterable of dependencies that are dynamically used.  Typically used for better introspection and for calculating the order of destroy operations.  Examples include:

        * Dependencies on specific :class:`~carthage.network.TechnologySpecificNetwork` implementations from machines.

        * Objects instantiated directly through ``get_instance`` or through :func:`resolve_deferred`

        '''
        return []
    

__all__ += ['Deployable']

class DeployableFinder(AsyncInjectable):

    '''
    A :class:`Deployable` represents some infrastructure that can be created by an Carthage object.
    There are multiple conventions for finding the available *Deployables*.  For example :class:`~carthage.machine.Machine` are typically found by searching for all ``InjectionKey(Machine)`` with a *host* constraint.

    a :class:`DeployableFinder` knows how to find some deployables in the injector hierarchy.

    DeployableFinders have a *name* typically set in a subclass.  It is an error to instantiate a subclass of DeployableFinder that is not refined enough to have a name.

    The default class injection key arranges that if a *DeployableFinder* is added to an injector, it will provide ``InjectionKey(DeployableFinder, name=name)``.  So, to instantiate all the DeployableFinders it is sufficient to::

        ainjector.filter_instantiate_async(DeployableFinder, ['name'])

    '''

    #: The plugin name under which a DeployableFinder is registered
    name = None

    async def find(self, ainjector):
        '''
        Returns an iterable of :class:`Deployable` objects. Thes objects should be instantiated in a not-ready state (instantiating InjectionKeys with ``_ready=False``.

        :param ainjector: The root of the hierarchy to search. *ainjector* must be an AsyncInjector.  If :meth:`Injector.filter` is called, then it should be called on *ainjector* and *ainjector* should be passed in as the *stop_at* parameter to :meth:`Injector.filter`.  *ainjector* may not be a parent of *self.ainjector*; consider the case when :func:`find_deployables` is called with *recurse* set to True.
        
        '''
        raise NotImplementedError

    def __init__(self, **kwargs):
        if self.name is None:
            raise TypeError(f'{self.__class__} is abstract: it has no DeployableFinder name')
        super().__init__(**kwargs)

    @classmethod
    def default_class_injection_key(cls):
        if cls.name is None:
            return super().default_class_injection_key()
        return InjectionKey(DeployableFinder, name=cls.name)

__all__ += ['DeployableFinder']

class MachineDeployableFinder(DeployableFinder):

    '''
    A :class:`DeployableFinder` that finds :class:`Machines <carthage.machine.Machine>`.

    Usage::
        injector.add_provider(MachineDeployableFinder)

    '''

    name = 'machine'

    async def find(self, ainjector):
        from .machine import Machine
        result = await ainjector.filter_instantiate_async(Machine, ['host'], stop_at=ainjector, ready=False)
        return [x[1] for x in result]

@inject(
    ainjector=AsyncInjector,
    )
async def find_deployables(
        *, ainjector,
        readonly=False,
        recurse=False,
        ):
    '''Find the deployables in an injector hierarchy.

    :returns: A list of :class:`Deployable`

    :param readonly: If True, then readonly is set to DryRun on
    instances after instantiation.  Finders will typically instantiate
    instances ``_ready=False``which typically means that readonly does
    get set in time.  However, if there is some dependency that is
    marked ``_ready=True`` then that dependency subtree will be
    instantiated to ready even when the root instantiation is
    ``_ready=False``.  In such cases, find_deployables may not set
    readonly until after the object is constructed. Layouts should
    carefully consider the implications of ``_ready=True``
    dependencies, because such dependencies may be deployed even on a
    ``readonly=True`` find_deployables call.  Even if
    ``readonly=False``, readonly will not be forced to False within
    instantiated objects.  Setting this parameter to True tries to
    force readonly mode; setting this parameter False respects objects
    that are marked readonly in the layout.

    :param recurse: If True, for each object returned by finders,
    apply the finders recursively (with stop_at set to the
    Deployable's injector).  Some objects such as
    :class:`carthage_aws.AwsNatGateway` optionally include
    dependencies based on their configuration.  For example, if no
    external address is provided, AwsNatGateway dynamically creates a
    :class:`carthage_aws.VpcAddress`.  When deploying, *recursive*
    should be set to False.  If Deployables need to deploy an optional
    dependency, they should do that as part of coming to ready.
    However, when looking for orphans, *recursive* and *readonly*
    should both be set to True, to probe for the largest set of
    potentially Deployed objects. If recursive is not set when
    searching for orphans, objects that are actually created by the
    layout may be flagged as orphans.

    '''
    async def do_recurse(stop_at):
        for finder in finders:
            for r in await finder.find(ainjector=stop_at):
                if id(r) in result_ids: continue
                results.append(r)
                result_ids.add(id(r))
                if readonly and not r.readonly:
                    r.readonly = DryRun
                if recurse:
                    futures.append(asyncio.ensure_future(
                        do_recurse(stop_at=r.ainjector)))

    finder_filter = await ainjector.filter_instantiate_async(DeployableFinder, ['name'], ready=True)
    futures = []
    # We want to make sure we return an object at most once, and make
    # sure we never recurse into an object more than once. We cannot
    # have a set of results.  The main reason is that we cannot
    # guarantee that all Deployables are hashable. The secondary
    # reason is that order may be desirable to preserve, although that
    # requires additional study. However id(obj) is hashable always,
    # so we can have a set of ids as a way to see if we have already
    # found an object.
    result_ids = set()
    results = []
    finders = [x[1] for x in finder_filter]
    await do_recurse(stop_at=ainjector)
    if futures:
        await asyncio.gather(*futures)
    return results

__all__ += ['find_deployables']

async def find_deployable(deployable: Deployable):
    '''
    Ideally, :meth:`Deployable.find` would return non-falsy if an object exists and falsy if it does not exist.  Unfortunately, some of the Carthage plugins do not follow this pattern.  This method:

    * Returns whatever find returns if it is non-falsy (I.E. objects exists)

    * Returns true if find returns falsy (typically None) and *deployable.mob* exists and is not None

    * Returns False otherwise

    '''
    result = await deployable.find()
    if bool(result): return result
    try:
        if deployable.mob is not None: return True
    except AttributeError: pass
    return False

__all__ += ['find_deployable']

def clear_dry_run_marker(deployables):
    for d in deployables:
        if d.readonly is DryRun:
            d.readonly = False
            if is_obj_ready(d):
                warnings.warn(f'{d!r} was already ready when clearing read only', stack_level=3)
                

@inject(ainjector=AsyncInjector)
async def run_deployment(
        *,
        dry_run=False,
        deployables: typing.Union[DeploymentResult, list[Deployable]] = None,
        filter=lambda d:True,
        ainjector):
    '''
    Run a deployment.

    #. Find the objects using :func:`find_deployables`

    #. For each object returned, call some deployment method on the object.  For an actual deployment, that is :meth:`async_become_ready`.

    :returns: A :class:`DeploymentResult` capturing the results of the deployment.  Will raise if find_deployments fails.

    :param dry_run: Do not actually call a deployment method, but instead report successes for all objects that would be touched.

    :param filter: If specified, a function that returns whether to operate on a given Deployable.

    :param deployables: If specified, then operate on the given deployables (after applying *filter*) rather than calling :func:`find_deployables`. If *deployables* is a :class:`DeploymentResult`, then operate on the successes of that result. When *deployables* is specified and *dry_run* is not true, any :class:`Deployable` with *readonly* set to *DryRun* will have readonly cleared.  Typical use is to run a deployment with *dry_run* set to True and then after confirming that the deployment is desired, to pass in the :class:`DeploymentResult` from the dry run into a new call to :func:`run_deployment`.
    
    '''
    find_readonly = dry_run
    match deployables:
        case DeploymentResult():
            deployables_list = deployables.successes
            if not dry_run: clear_dry_run_marker(deployables_list)
        case list():
            deployables_list = deployables
            if not dry_run: clear_dry_run_marker(deployables_list)
        case _:
            deployables_list = await ainjector(
                find_deployables,
                readonly=find_readonly,
                # Not recursive until we are looking at delete_orphans.
            )
    result = DeploymentResult()
    futures = []
    with DeploymentIntrospection(ainjector.injector, 'deploy', result):
        for d in deployables_list:
            if not dry_run:
                if d.readonly:
                    future = asyncio.ensure_future(ainjector(
                        find_deployable, d))
                    future.add_done_callback(result.find_callback(d))
                    futures.append(future)
                else:
                    future = asyncio.ensure_future(d.async_become_ready())
                    future.add_done_callback(result.method_callback(d))
                    futures.append(future)
            else: # dry_run
                if d not in result:
                    result.successes.append(d)

        if futures:
            await asyncio.wait(futures) # We should already have captured in result
    return result

__all__ += ['run_deployment']

@inject(ainjector=AsyncInjector)
async def find_deployables_reverse_dependencies(ainjector):
    def add_dependency(depending, on, /):
        if isinstance(on, Deployable):
            reverse_dependencies.setdefault(on, set())
            reverse_dependencies[on] |= {depending}
    reverse_dependencies = {}
    deployables = await ainjector(find_deployables,
                                  recurse=True,
                                  readonly=True)
    for d in deployables:
        if hasattr(d, 'dynamic_dependencies'):
            try:
                for dependency in await d.dynamic_dependencies():
                    add_dependency(d, dependency)
            except:
                logger.exception('Error finding dynamic dependencies for %s', d)
        dependency_introspection.calculate_reverse_dependencies(
            d, injector=d.injector,
            reverse_dependencies=reverse_dependencies,
            filter=lambda d:isinstance(d,Deployable))
    return reverse_dependencies

__all__ += ['find_deployables_reverse_dependencies']
