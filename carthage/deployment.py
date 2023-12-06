# Copyright (C) 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import enum
import typing
from .dependency_injection import *

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
        If this returns True, the object must exist.  If this returns falsy, then if *id* or *mob* is set on the object,  it exists.  There is a bit of divergence in how :meth:`find` returns.  See :func:`find_deployable` for a more uniform way to call this function.
        Should not raise simply because the object does not exist.
        '''
        raise NotImplementedError


    #: If True,do not deploy the object bringing it to Ready; used for
    # examining existing objects without creating new objects.
    # Instantiating an object readonly does not stop methods like
    # :meth:`delete` from changing the state of the object.  It only
    # affects what the instantiation process does.
    readonly: bool
    

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

    :param readonly: If True, then readonly is set to True on
    instances after instantiation.  Finders will typically instantiate
    instances ``_ready=False``which typically means that readonly does
    get set in time.  However, if there is some dependency that is
    marked ``_ready=True`` then that dependency subtree will be
    instantiated to ready even when the root instantiation is
    ``_ready=False``.  Layouts should carefully consider the
    implications of ``_ready=True`` dependencies, because such
    dependencies may be deployed even on a ``readonly=True``
    find_deployables call.  Even if ``readonly=False``, readonly will
    not be forced to False within instantiated objects.  Setting this
    parameter to True tries to force readonly mode; setting this
    parameter False respects objects that are marked readonly in the
    layout.

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
                if readonly: r.readonly = True
                if recurse:
                    futures.append(asyncio.ensure_future(
                        do_recurse(stop_at=r.ainjector)))

    finder_filter = await ainjector.filter_instantiate_async(DeployableFinder, ['name'], ready=True)
    futures = []
    result_ids = set()
    results = []
    finders = [x[1] for x in finder_filter]
    await do_recurse(stop_at=ainjector)
    if futures:
        await asyncio.gather(*futures)
    return results

__all__ += ['find_deployables']
