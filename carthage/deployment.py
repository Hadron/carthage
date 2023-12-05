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

    async def find(self, stop_at):
        '''
        Returns an iterable of :class:`Deployable` objects. Thes objects should be instantiated in a not-ready state (instantiating InjectionKeys with ``_ready=False``.

        :param stop_at: The scope of the injector hierarchy to search.  See :meth:`carthage.Injector.filter` for documentation.
        
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

    async def find(self, stop_at):
        from .machine import Machine
        result = await self.ainjector.filter_instantiate_async(Machine, ['host'], stop_at=stop_at, ready=False)
        return [x[1] for x in result]

@inject(
    ainjector=AsyncInjector,
    )
async def find_deployables(*, ainjector,
                           stop_at=None):
    finder_filter = await ainjector.filter_instantiate_async(DeployableFinder, ['name'],  stop_at=stop_at, ready=True)
    result_ids = set()
    results = []
    for _, finder in finder_filter:
        for r in await finder.find(stop_at=stop_at):
            if id(r) in result_ids: continue
            results.append(r)
            result_ids.add(id(r))
    return results

__all__ += ['find_deployables']
