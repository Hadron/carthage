# Copyright (C) 2019, 2020, 2021, 2022, 2023, 2024, 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import asyncio
import collections.abc
import contextlib
import dataclasses
import datetime
import importlib.resources
import logging
import os
import os.path
import time
import typing
import sys
import shutil
import weakref
import hashlib
from pathlib import Path
import carthage
from carthage.dependency_injection import AsyncInjector, AsyncInjectable, inject, BaseInstantiationContext, InjectionKey, NotPresent, inject_autokwargs, Injectable, Injector
from carthage.dependency_injection.introspection import current_instantiation
from carthage.config import ConfigLayout
from carthage.utils import memoproperty, import_resources_files

__all__ = ['logger', 'PathMixin', 'TaskWrapper', 'TaskMethod', 'setup_task', 'SkipSetupTask', 'SetupTaskMixin',
           'cross_object_dependency',
           'mako_task',
           'install_mako_task']

logger = logging.getLogger('carthage.setup_tasks')

@inject_autokwargs(injector=Injector)
class PathMixin(Injectable):
    '''
    Provide the commond :meth:`stamp_path`, :meth:`log_path`, and :meth:`state_path`.
    Requires that subclasses fill in :attr:`stamp_subdir`.

    Also provides :attr:`config_layout`.
    

    '''

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if not hasattr(self, 'config_layout'):
            self.config_layout = self.injector(ConfigLayout)
            
    @property
    def stamp_subdir(self):
        '''The part of :meth:`stamp_path` after *config.stamp_dir*.
        For example for a podman container called ``example.com`` this might be ``podman/containers/example.com``
        '''
        raise NotImplementedError

    @memoproperty
    def stamp_path(self)->Path:
        '''A  place to store cached information like completion stamps for setup tasks.
        Removing *config.cache_dir* may decrease performance but should not remove hard-to-recover state like keys.
        '''
        res =  Path(self.config_layout.cache_dir)/self.stamp_subdir
        res.mkdir(parents=True, exist_ok=True)
        return res

    @memoproperty
    def state_path(self)->Path:
        '''
        A place to store state that should be preserved like keys, tokens, or assignment databases.
        '''
        res =  Path(self.config_layout.state_dir)/self.stamp_subdir
        res.mkdir(parents=True, exist_ok=True)
        return res

    @memoproperty
    def log_path(self)->Path:
        '''
        A place to store logs.
        '''
        res = Path(self.config_layout.log_dir)/self.stamp_subdir
        res.mkdir(parents=True, exist_ok=True)
        return res

    def clear_stamps_and_cache(self):
        cache_dir = Path(self.config_layout.cache_dir)
        if str(self.stamp_path).startswith(str(cache_dir)):
            shutil.rmtree(self.stamp_path, ignore_errors=True)
            try:
                del self.stamp_path # so it gets recreated
            except (AttributeError, TypeError): pass
        else:
            logger.warn('Failed to clear stamps for %s: stamps not under cache_dir', self)
            
_task_order = 0


def _inc_task_order():
    global _task_order
    t = _task_order
    _task_order += 100
    return t


class SetupTaskContext(BaseInstantiationContext):

    instance: 'SetupTaskMixin'
    task: 'TaskWrapperBase'
    dependencies_have_run: bool #: Have SystemDependencies marked with depend_on been run
    
    def __init__(self, instance, task):
        super().__init__(instance.injector)
        self.instance = instance
        self.task = task
        self.dependencies_have_run = False

    def __enter__(self):
        res = super().__enter__()
        if self.parent:
            self.parent.dependency_progress(self.task.stamp, self)
        return res

    def done(self):
        if self.parent:
            self.parent.dependency_final(self.task.stamp, self)
        super().done()

    @property
    def description(self):
        return f'setup_task: {self.instance}.{self.task.stamp}'

    def get_dependencies(self):
        from .dependency_injection.introspection import get_dependencies_for
        return get_dependencies_for(self.task.func, self.instance.injector)

    async def call_dependencies_once(self):
        '''
        If dependencies have not already been called, then  call them.
        See :meth:`TaskWrapperBase.depend_on`.
        This function is not reentrant; it is intended to avoid the task calling dependencies if sholud_run has already done so.
        '''
        if self.dependencies_have_run: return
        await self.task.call_dependencies(self.instance)
        self.dependencies_have_run = True
        
@dataclasses.dataclass
class TaskInspector:

    task: TaskWrapperBase
    #: Either the class or instance with the task
    from_obj: typing.Union[SetupTaskMixin, type[SetupTaskMixin]]
    #: The task previous in the dependency chain.
    previous: typing.Optional[TaskInspector] = dataclasses.field(default=None, repr=False)

    @inject(ainjector=AsyncInjector)
    async def should_run(self, ainjector):
        if not self.is_instance:
            raise NotImplementedError('You must inspect an instantiated class to determine if a task will run.')
        dependency_last_run = await self.dependency_last_run(ainjector)
        should_run, last_run = await  self.task.should_run_task(self.from_obj, ainjector=ainjector, run_methods=False)
        self.last_run = last_run
        return should_run

    @property
    def stamp(self):
        "The complete stamp that will be checked; for customizations includes the stamp_stem"
        try: return self._stamp
        except AttributeError:
            return self.task.stamp

    @stamp.setter
    def stamp(self, val):
        self._stamp = val
        

    @property
    def instance_id(self):
        "The ID of the instance on which this task is run; for Customizations, the ultimate host's ID, **not** the id of the instantiated :class:`carthage.machine.BaseCustomization`"
        try: return self._instance_id
        except AttributeError:
            return id(self.from_obj)

    @instance_id.setter
    def instance_id(self, val):
        self._instance_id = val
        
    def subtasks(self):
        from .machine import CustomizationWrapper
        if isinstance(self.task, CustomizationWrapper):
            return self.task.inspect(self.from_obj, instance_id=self.instance_id)
        return []
    
    async def dependency_last_run(self, ainjector):
        try: return self._dependency_last_run
        except AttributeError: pass
        inspector_list = []
        current = self.previous
        dependency_last_run = None
        while current:
            try:
                dependency_last_run = inspector.last_run
                break
            except AttributeError:pass
            inspector_list.insert(0,current)
            current = current.previous
        for inspector in inspector_list:
            if dependency_last_run and not hasattr(inspector, '_dependency_last_run'):
                inspector._dependency_last_run = dependency_last_run
            await inspector.should_run(ainjector)
            dependency_last_run = inspector.last_run
        if dependency_last_run is None:
            # Happens when no previous dependencies
            dependency_last_run = 0.0
        self._dependency_last_run = dependency_last_run
        return dependency_last_run

    @property
    def description(self):
        return f'{self.task.description} for {self.from_obj}'

    @property
    def is_instance(self):
        '''Return true  if this is inspecting an instance  that has been instantiated rather than inspecting tasks on a class.
        '''
        from .machine import CustomizationInspectorProxy
        return isinstance(self.from_obj, (SetupTaskMixin, CustomizationInspectorProxy))

                
    

@dataclasses.dataclass
class TaskWrapperBase:

    description: str
    order: int = dataclasses.field(default_factory=_inc_task_order)
    invalidator_func = None
    check_completed_func = None
    hash_func: typing.Callable = staticmethod(lambda self: "")
    dependencies: list = dataclasses.field(default_factory=lambda: [])

    #: If True, then dependencies will be
    #called/entered while validating whether the task should run.
    #That is these dependencies will be available when invalidators
    #and check_completed functions are called.  If the outcome of
    #should_run can be determined without calling invalidators or
    #check_completed functions, dependencies may not be called.
    dependencies_always: bool = False

    @memoproperty
    def stamp(self):
        raise NotImplementedError

    def __set_name__(self, owner, name):
        self.stamp = name

    def __get__(self, instance, owner):
        if instance is None:
            return self
        return TaskMethod(self, instance)

    async def __call__(self, instance, *args, **kwargs):
        async def success():
            if not self.check_completed_func:
                hash_contents = await instance.ainjector(self.hash_func, instance)
                instance.create_stamp(self.stamp, hash_contents)
            instance.injector.emit_event(
                InjectionKey(SetupTaskMixin), "task_ran",
                instance, task=self,
                adl_keys=instance.setup_task_event_keys())
            
        def fail(e):
            if not self.check_completed_func:
                instance.logger_for().warning(
                    f'Deleting {self.description} task stamp for {instance} because task failed')
                instance.delete_stamp(self.stamp)
            instance.injector.emit_event(
                InjectionKey(SetupTaskMixin), "task_fail",
                instance, task=self,
                exception=e,
                adl_keys=instance.setup_task_event_keys())
    

        mark_context_done = True
        with contextlib.ExitStack() as stack:
            context = current_instantiation()
            if isinstance(context, SetupTaskContext) \
               and context.instance is instance and context.task is self:
                mark_context_done = False # Will be handled by caller
            else:
                context = SetupTaskContext(instance, self)
                stack.enter_context(context)
            try:
                await context.call_dependencies_once()
                res = self.func(instance, *args, **kwargs)
                if isinstance(res, collections.abc.Coroutine):
                    instance.injector.emit_event(
                        InjectionKey(SetupTaskMixin), "task_start",
                        instance, task=self,
                        adl_keys=instance.setup_task_event_keys())
                    res = await res

                await success()
                return res
            except SkipSetupTask:
                raise
            except Exception as e:
                fail(e)
                raise
            finally:
                if mark_context_done:
                    context.done()

    async def should_run_task(self, obj: SetupTaskMixin,
                              dependency_last_run: float = None,
                              *, ainjector: AsyncInjector, introspection_context:SetupTaskContext = None,
                              run_methods=True):

        '''Indicate whether this task should be run for *obj*.

        :returns: Tuple of whether the task should be run and when the task was last run if ever.

        * If :meth:`check_completed` has been called, then the task should be run when either the check_completed function returns falsy or our dependencies have been run more recently.

        * Otherwise, if there is no stamp then this task should run

        * If there is a :meth:`invalidator`, then this task should run if the invalidator returns falsy.

        * This task should run if any dependencies have run more recently than the stamp

        * If there is a nontrivial hash_func, then the hash contents
          stored in the stamp are compared to the results of the
          hash_func.  If the Contents differ, this task should run.

        * Otherwise this task should not run.

        :param: obj
            The instance on which setup_tasks are being run.
        :param run_methods: If False , no check_completed or invalidators are run.  If the task has a check_completed function, return None rather than True or False for wether the task should run.  Invalidators and hash functions are ignored, and the return is dependent only on stamps.

        '''
        def _h(s):
            '''Shorten cases where hash_func returns a long string rather than a hash
            '''
            if len(s) > 65:     # sha256 plus a newline
                return hashlib.sha256(s.encode()).hexdigest()
            return s
        if dependency_last_run is None:
            dependency_last_run = 0.0
        if self.check_completed_func:
            if not run_methods: return (None, dependency_last_run)
            if self.dependencies_always and introspection_context:
                await introspection_context.call_dependencies_once()
            last_run = await ainjector(self.check_completed_func, obj)
            hash_contents = ""
            if last_run is True:
                obj.logger_for().debug(f"Task {self.description} for {obj} determined complete by check_completed_func(); no timestamp provided")
                return (False, dependency_last_run)
        else:
            last_run, hash_contents = obj.check_stamp(self.stamp)
        if last_run is False:
            obj.logger_for().debug(f"Task {self.description} never run for {obj}")
            return (True, dependency_last_run)
        if last_run < dependency_last_run:
            obj.logger_for().debug(
                f"Task {self.description} last run {_iso_time(last_run)}, but dependency run more recently at {_iso_time(dependency_last_run)}")
            return (True, dependency_last_run)
        if (not self.check_completed_func) and run_methods:
            if self.dependencies_always and introspection_context:
                await introspection_context.call_dependencies_once()
            actual_hash_contents = await ainjector(self.hash_func, obj)
            if actual_hash_contents != hash_contents:
                obj.logger_for().info(f'Task {self.description} invalidated by hash_func() change from `{_h(hash_contents)}` to `{_h(actual_hash_contents)}`; last run {_iso_time(last_run)}')
                return (True, dependency_last_run)
        if self.invalidator_func and run_methods:
            if self.dependencies_always and introspection_context:
                await introspection_context.call_dependencies_once()
            if not await ainjector(self.invalidator_func, obj, last_run=last_run):
                obj.logger_for().info(f"Task {self.description} invalidated for {obj} by invalidator_func(); last run {_iso_time(last_run)}")
                return (True, time.time())
        obj.logger_for().debug(f"Task {self.description} last run for {obj} at {_iso_time(last_run)}; re-running not required")
        return (False, last_run)

    async def call_dependencies(self, instance):
        '''
        Make this tasks dependencies available; see :meth:`depend_on`
        '''
        ainjector = instance.ainjector
        futures = []
        for d in self.dependencies:
            future = asyncio.ensure_future(ainjector(d, ainjector=ainjector))
            futures.append(future)
        if futures:
            await asyncio.gather(*futures)
            
    def depend_on(self, dependency):
        '''
        Decorator to  mark a particular task as depending on some :class:`~carthage.SystemDependency`.  Example Usage::

            @setup_task("upload_files")
            def upload_files(self):
                # . . .

            upload_files.depend_on(MachineDependency("fileserver"))

        This requires that when the *upload_files* task runs, the "fileserver" machine is both brought to ready and started.  If :attr:`dependencies_always` is True,  then dependencies will be ready for  :meth:invalidators <invalidator>` and :meth:`check completed functions <check_completed>` as well.

        Compare to :class:`cross_object_dependency` which sets up a dependency such that tasks in one object will re-run if tasks in another object have run more recently.
        '''
        from .system_dependency import SystemDependency
        assert isinstance(dependency, SystemDependency)
        self.dependencies.append(dependency)
        
    def invalidator(self, slow=False):
        '''Decorator to indicate  an invalidation function for a :func:`setup_task`

        This decorator indicates a function that will validate whether some setup_task has successfully been created.  As an example, if a setup_task creates a VM, an invalidator could invalidate the task if the VM no longer exists.  Invalidators work as an additional check along side the existing mechanisms to track which setup_tasks are run.  Even if an invalidator  would not invalidate a task, the task would still be performed if its stamp does not exist.  Compare :meth:`check_completed` for a mechanism to exert direct control over whether a task is run.

        :param: slow
            If true, this invalidator is slow to run and should only be run if ``config.expensive_checks`` is True.

        Invalidators should return something True if the task is valid and something falsy to invalidate the task and request that the task and all dependencies be re-run.

        Usage example::

            @setup_task("create_vm)
            async def create_vm(self):
                # ...
            @create_vm.invalidator()
            async def create_vm(self, **kwargs):
                # if VM exists return true else false

        The invalidator receives the following keyword arguments;
        invalidators should be prepared to receive unknown arguments:

        last_run
            The time at which the task was last successfully run


        '''
        def wrap(f):
            self.invalidator_func = f
            return self
        return wrap

    def hash(self):
        '''Provides a mechanism for rerunning setup_tasks when inputs have changed.  Usage::

            @setup_task("do something")
            def do_something(self):
                # do stuff

            @do_something.hash()
            do_something(self):
                # return a rapid hash of the major inputs on which the do_something tasks varies

        Hash functions are entirely ignored when *check_completed* is
        used.  The hash function is called every time Carthage wishes
        to know whether the task has completed, so it needs to be fast
        to compute.  The result of the hash function is stored in the
        completion stamp on successful completion.  On later runs, the
        result of the hash function is checked against the completion
        stamp.  If these two results differ, the task is rerun.
        '''
        def wrap(func):
            self.hash_func = func
            return self
        return wrap

    def check_completed(self):
        '''Decorator to provide function indicating whether a task has already been done

        Usage::

            @setup_task("task")
            async def setup_something(self):
                # do stuff
            @setup_something.check_completed()
            def setup_something(self):
                # Return :func:`time.time` when the task was completed or None or true
                # If True is returned, then task is marked completed, but will not work well with dependencies

        '''
        def wrap(f):
            self.check_completed_func = f
            return self
        return wrap


class TaskWrapper(TaskWrapperBase):

    def __init__(self, func, **kwargs):
        super().__setattr__('func', func)
        super().__init__(**kwargs)

    @memoproperty
    def stamp(self):
        return self.func.__name__

    def __getattr__(self, a):
        if a == "func":
            raise AttributeError
        return getattr(self.func, a)

    extra_attributes = frozenset()

    def __setattr__(self, a, v):
        if a in ('func',
                 'dependencies_always', 'stamp', 'order',
                 'invalidator_func', 'check_completed_func', 'hash_func') or a in self.__class__.extra_attributes:
            return super().__setattr__(a, v)
        else:
            return setattr(self.func, a, v)

    @property
    def __wraps__(self):
        return self.func


class TaskMethod:

    def __init__(self, task, instance):
        self.task = task
        self.instance = instance

    def __call__(self, *args, **kwargs):
        return self.task(self.instance, *args, **kwargs)

    def __getattr__(self, a):
        return getattr(self.task.func, a)

    def __repr__(self):
        return f"<TaskMethod {self.task.stamp} of {self.instance}>"


def setup_task(description, *,
               order=None,
               before=None):
    '''Mark a method as a setup task.  Describe the task for logging.  Must be in a class that is a subclass of
    SetupTaskMixin.  Usage::

        @setup_task("unpack"
        async def unpack(self): ...

    :param order: Overrides the order in which tasks are run; an integer; lower numbered tasks are run first, higher numbered tasks are run later.  It is recommended that task ordering be a total ordering, but this is not a requirement.  It is an error if both *order* and *before* are set.

    :param before: Run this task before the task referenced in *before*.

    '''
    global _task_order
    if order and before:
        raise TypeError('Order and before cannot both be specified')
    if before:
        order = before.order - 1
    if order and order > _task_order:
        _task_order = order
        _inc_task_order()

    def wrap(fn):
        kws = {}
        if order:
            kws['order'] = order
        t = TaskWrapper(func=fn, description=description, **kws)
        return t
    return wrap


class SkipSetupTask(Exception):
    pass


class SetupTaskMixin(PathMixin, AsyncInjectable):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_tasks = sorted(self.class_setup_tasks(),
                                  key=lambda t: t.order)

    def add_setup_task(self, task, **kwargs):
        if isinstance(task, TaskWrapperBase):
            if kwargs:
                raise RuntimeError('kwargs cannot be specified if task is a TaskWrapper')
        else:
            stamp = kwargs.pop('stamp', None)
            task = TaskWrapper(func=task, **kwargs)
            if stamp:
                task.stamp = stamp
        self.setup_tasks.append(task)
        # xxx reorder either here or in run_setup_tasks

    @memoproperty
    def readonly(self):
        '''
        If ``bool(readonly)``, then automated Carthage functions should not disturb the real state of the object:

        * run_setup_tasks will not run setup tasks

        * The deployment engine will not deploy  or destroy the object

        * Carthage plugins should not create the object.

        Explicit calls to methods like :meth:`delete`  or :meth:`do_create` will still affect the state of the object.

        By default, readonly looks up ``InjectionKey('readonly')`` in the object's injector, and returns False if that injection key is not provided. As a consequence:

        * Setting readonly in the class body of a modeling object affects readonly for that object and any object that inherits from its injector.

        * Setting readonly in a non-modeling class body sets the default readonly state for objects of that class and detaches those objects from looking at their injectors to determine readonly. This should generally be avoided as it produces surprising behavior.

        * Setting readonly on an instance sets the readonly state for that object and detaches the object from tracking injectors.

        * Setting the readonly injection key on an injector affects objects downstream of that object that have not already cached their readonly state.

        '''
        readonly = self.injector.get_instance(InjectionKey('readonly', _optional=NotPresent))
        if readonly is NotPresent:
            return False
        return readonly
    
    async def run_setup_tasks(self, context=None):
        '''Run the set of collected setup tasks.  If *context* is provided, it
        is used as an asynchronous context manager that will be entered before the
        first task and eventually exited.  The context is never
        entered if no tasks are run.
        This execution context is different from :class:`SetupTaskContext`. The *SetupTaskContext* is an introspection mechanism that tracks which setup task is running and why; the asynchronous context allows a set of tasks for example in a customization to have common resources available.
        
        '''
        injector = getattr(self, 'injector', carthage.base_injector)
        ainjector = getattr(self, 'ainjector', None)
        if ainjector is None:
            ainjector = injector(AsyncInjector)
        config = getattr(self, 'config_layout', None)
        if config is None:
            config = injector(ConfigLayout)
        context_entered = False
        dry_run = config.tasks.dry_run
        dependency_last_run = 0.0
        if self.readonly:
            self.logger_for().info('Not running tasks for %s which is readonly', self)
            return
        for t in self.setup_tasks:
            with SetupTaskContext(self, t) as introspection_context:
                try:
                    should_run, dependency_last_run = await t.should_run_task(self, dependency_last_run, ainjector=ainjector, introspection_context=introspection_context)
                except:
                    introspection_context.done()
                    if context_entered:
                        await context.__aexit__(*sys.exc_info())
                    raise
                if should_run:
                    self.injector.emit_event(
                        InjectionKey(SetupTaskMixin), "task_should_run",
                        self, task=t,
                        adl_keys=self.setup_task_event_keys())
                    try:
                        if (not context_entered) and context is not None:
                            await context.__aenter__()
                            context_entered = True
                        if not dry_run:
                            self.logger_for().info(f"Running {t.description} task for {self}")
                            started = time.time()
                            await ainjector(t, self)
                            dependency_last_run = time.time()
                            a = datetime.datetime.fromtimestamp(started)
                            b = datetime.datetime.fromtimestamp(dependency_last_run)
                            self.logger_for().info(f"Finished running {t.description} task for {self} from {a.time()} to {b.time()} ({b - a})")
                        else:
                            self.logger_for().info(f'Would run {t.description} task for {self}')
                    except SkipSetupTask:
                        pass
                    except Exception:
                        self.logger_for().exception(f"Error running {t.description} for {self}:")
                        if context_entered:
                            await context.__aexit__(*sys.exc_info())
                        raise
                    finally:
                        introspection_context.done()
                else:           # not should_run
                    self.injector.emit_event(
                        InjectionKey(SetupTaskMixin), "task_already_run",
                        self, task=t,
                        adl_keys=self.setup_task_event_keys())
                    introspection_context.done()
        if context_entered:
            await context.__aexit__(None, None, None)

    def setup_task_event_keys(self):
        '''Yield the set of keys that setup_task related events should be dispatc should be dispatched to.  In addition to keys yi.yielded by this generator, all setup tasks events are dispatched to InjectionKey(SetupTaskMixin).
        '''
        yield self.default_class_injection_key()
        yield from self.supplementary_injection_keys(self.default_class_injection_key())
        
        
    @classmethod
    def class_setup_tasks(cls):
        '''
        returns a tuple of TaskWrapperBases associated with *cls*.  Handles MRO and methods overriding tasks.
        This is a relatively expensive operation, so it is memoized per class.
        '''
        try:  return cls.__dict__['_class_setup_tasks_prop']
        except (AttributeError, KeyError): pass
        results: list[TaskWrapperBase] = []
        meth_names = {}
        for c in cls.__mro__:
            if not issubclass(c, SetupTaskMixin):
                continue
            for m in c.__dict__:
                if m in meth_names:
                    continue
                meth = getattr(c, m)
                meth_names[m] = True
                if isinstance(meth, TaskWrapperBase):
                    results.append(meth)

        cls._class_setup_tasks_prop = tuple(results)
        return cls.__dict__['_class_setup_tasks_prop']
    
    async def async_ready(self):
        '''
        This may need to be overridden, but is provided as a default
        '''
        await self.run_setup_tasks()
        return await super().async_ready()

    def create_stamp(self, stamp, contents):
        try:
            with open(os.path.join(self.stamp_path, ".stamp-" + stamp), "wt") as f:
                # on NFS, opening a zero-length file even for truncate does not reset the utime
                os.utime(f.fileno())
                if contents:
                    f.write(contents)
        except FileNotFoundError:
            os.makedirs(self.stamp_path, exist_ok=True)
            with open(os.path.join(self.stamp_path, ".stamp-" + stamp), "wt") as f:
                os.utime(f.fileno())
                if contents:
                    f.write(contents)

    def delete_stamp(self, stamp):
        try:
            os.unlink(os.path.join(self.stamp_path, ".stamp-" + stamp))
        except FileNotFoundError:
            pass

    def check_stamp(self, stamp, raise_on_error=False):
        '''
        :returns: a tuple containing the unix time of the stamp and the text contents of the stamp.  The first element of the tuple is False if the stamp does not exist
        '''
        if raise_on_error not in (True, False):
            raise SyntaxError(f'raise_on_error must be a boolean. current value: {raise_on_error}')
        try:
            if not self.stamp_path: raise FileNotFoundError
            path = Path(self.stamp_path) / f'.stamp-{stamp}'
            res = os.stat(path)
        except FileNotFoundError:
            if raise_on_error:
                raise RuntimeError(f"stamp directory '{self.stamp_path}' did not exist") from None
            return (False, "")
        return res.st_mtime, path.read_text()

    def logger_for(self):
        try:
            return self.logger
        except AttributeError:
            return logger

    def inspect_setup_tasks(self, *, stamp_stem="", instance_id=None):
        '''Iterates over the setup tasks of a ninstance and provides an inspector that can determine if a task would run and what its description is.
        '''
        prev = None
        for t in self.setup_tasks:
            prev = TaskInspector(task=t, from_obj=self, previous=prev)
            if stamp_stem: prev.stamp = stamp_stem+prev.stamp
            if instance_id: prev.instance_id = instance_id
            yield prev

    @classmethod
    def inspect_class_setup_tasks(cls):
        '''Iterates over all the setup tasks that instances of this class will have and provides an inspector for that task.
        '''
        prev = None
        for t in cls.class_setup_tasks():
            prev=TaskInspector(task=t, from_obj=cls, previous=prev)
            yield prev
            

def _iso_time(t):
    return datetime.datetime.fromtimestamp(t).isoformat()


class cross_object_dependency(TaskWrapper):

    '''
    Usage::

        # in a client machine's class
        fileserver_dependency = cross_object_dependency(FileServer.update_files, 'fileserver')

    :param task: a :class:`TaskWrapper`, typically associated with another class, or alternatively the name of an attribute on *relationship* containing either a :class:`TaskWrapper` or :class:`TaskMethod`.

    :param relationship: The string name of a relationship such that calling the *relationship* method on an instance containing this dependency will yield the instance containing *task* that we want to depend on.

    A cross object dependency will invalidate later tasks in this object if the depended on task has run more recently than these tasks.  That is in the example above, if the fileserver's *update_files* task has run more recently than this object's setup_tasks, then re-run this object's setup_tasks.

    Compare to :meth:`TaskWrapper.depend_on`, which guarantees that when a particular task is running, certain dependencies are available.
    
    '''

    dependent_task: typing.Union[TaskWrapper, TaskMethod, str]
    relationship: str

    def __init__(self, task, relationship, **kwargs):
        if isinstance(task,str):
            description = task
        else: description=task.description
        super().__init__(func=lambda self: None,
                         description=f'Dependency on `{description}\' task of {relationship}',
                         **kwargs)
        self.dependent_task = task
        self.relationship = relationship

    def _handle_task(self, instance):
        if isinstance(self.dependent_task, str):
            relationship = getattr(instance, self.relationship)
            self.dependent_task = getattr(relationship, self.dependent_task)
            if isinstance(self.dependent_task, TaskMethod):
                self.dependent_task = self.dependent_task.task
                
    @inject(ainjector=AsyncInjector)
    async def check_completed_func(self, instance, ainjector):
        self._handle_task(instance)
        task = self.dependent_task
        should_run, last_run = await task.should_run_task(getattr(instance, self.relationship), ainjector=ainjector)
        # We don't care about whether the task would run again, only when it last run.
        if last_run > 0.0:
            return last_run
        # We have no last_run info so we don't know that we need to trigger a re-run
        return True

    def __repr__(self):
        if isinstance(self.dependent_task, str):
            description = self.dependent_task
        else: description = self.dependent_task.description
        return f'<Depend on {description} task of {self.relationship}>'


class mako_task(TaskWrapper):

    '''
    Usage::

        dnsmasq_task = mako_task('dnsmasq.conf.mako',
            network = InjectionKey("some_network"))

    Typically used in a :class:`~carthage.modeling.MachineModel`.  Introduces a setup task to render a mako template.  Extra keyword arguments can be :class:`InjectionKey` in which case they are instantiated in the context of the injector of the object to which the setup task is attached.  These arguments are made available in the mako template context.  The *instance* template context argument is introduced and points to  the object on which the setup task is run. Arguments that are not InjectionKeys are passed directly to the template.

If the template has a def called *hash*, this def will be rendered with the same arguments as the main template body.  This value will be stored in the completion stamp; if the hash changes, the template will be re-rendered.  For performance reasons, try to keep the hash easy to compute.

    '''

    template: str
    output: str

    extra_attributes = frozenset({'template', 'output',
                                  })

    def __init__(self, template, output=None, **injections):
        kwargs = {}
        # Split kwargs; Leading _ is left as arguments to setup_task,
        # InjectionKeys are injections; other args are passed through
        template_args = {}
        for k in list(injections.keys()):
            if k.startswith("_"):
                kwargs[k[1:]] = injections.pop(k)
            elif not isinstance(injections[k], InjectionKey):
                template_args[k] = injections.pop(k)

        # A separate function so that injection works; consider
        # TaskMethod.__setattr__ to understand.
        @inject(**injections)
        def func(*args, **kwargs):
            return self.render(*args, **kwargs, **template_args)

        @inject(**injections)
        def hash_func(instance, **kwargs):
            template = self.lookup.get_template(self.template)
            if template.has_def('hash'):
                hash_template = template.get_def("hash")
                return hash_template.render(instance=instance, **kwargs, **template_args)
            else:
                return template.render(instance=instance, **kwargs, **template_args)
        self.template = template
        if output is None:
            output = template
            if output.endswith('.mako'):
                output = output[:-5]
        self.output = output
        super().__init__(func=func,
                         description=f'Render {self.template} template',
                         hash_func=hash_func,
                         **kwargs)

    def __set_name__(self, owner, name):
        super().__set_name__(owner, name)
        import sys
        import mako.lookup
        module = sys.modules[owner.__module__]
        try:
            self.lookup = module._mako_lookup
        except AttributeError:
            if hasattr(module, '__path__'):
                resources = importlib.resources.files(module)
            elif module.__package__ == "":
                resources = Path(module.__file__).parent
            else:
                resources = importlib.resources.files(module.__package__)
            templates = resources / 'templates'
            if not templates.exists():
                templates = resources
            module._mako_lookup = mako.lookup.TemplateLookup([str(templates)], strict_undefined=True)
            self.lookup = module._mako_lookup

    def render(task, instance, **kwargs):
        template = task.lookup.get_template(task.template)
        output = Path(instance.stamp_path).joinpath(task.output)
        os.makedirs(output.parent, exist_ok=True)
        with open(output, "wt") as f:
            f.write(template.render(
                instance=instance,
                **kwargs))


def find_mako_tasks(tasks):
    for t in tasks:
        if isinstance(t, mako_task):
            yield t


def install_mako_task(relationship, cross_dependency=True, *, relationship_ready=True):
    '''
:param relationship: The name of an attribute property containing :class:`mako_tasks <mako_task>` in its :meth:`~SetupTaskMixin.setup_tasks`.

    :param cross_dependency: If true (the default), rerun the installation whenever any of the underlying mako_tasks change.

    :param relationship_ready:  Bring the relationship to *async_become_ready* state before  installing mako tasks or checking cross-object dependencies.  Bringing the relationship to ready state allows the hash block in the mako template to access parts of the instance that are set up as part of bringing the instante to ready.  It also guarantees that the mako templates will be generated at least once.  However, it can produce dependency loops that hang Carthage in some circumstances.
    
    This task is generally associated on a machine to install mako templates rendered on the model.  Typical usage might look like::

        install_mako = install_mako_task('model')

    '''
    if relationship == 'self' and relationship_ready:
        # We could just set relationship_ready to false but if we do that in thde easy case of self, we will miss harder cases like host.
        raise RuntimeError('Set relationship_ready=False when using self as a relationship')
    
    @setup_task("Install mako templates")
    async def install(self):
        async with self.filesystem_access() as fspath:
            if relationship == 'self':
                related = self
            else:
                related = getattr(self, relationship)
            if relationship_ready: await related.async_become_ready()
            base = Path(related.stamp_path)
            path = Path(fspath)
            for mt in find_mako_tasks(related.setup_tasks):
                if os.path.isabs(mt.output):
                    logger.warn(f'{mt} has absolute path; skipping install')
                    continue
                src = base / mt.output
                dest = path / mt.output
                os.makedirs(dest.parent, exist_ok=True)
                shutil.copy2(src, dest)
    if cross_dependency:
        @install.invalidator()
        @inject(ainjector=AsyncInjector)
        async def install(self, ainjector, last_run, **kwargs):
            if relationship == 'self':
                related = self
            else:
                related = getattr(self, relationship)
            if relationship_ready: await related.async_become_ready()
            last = 0.0
            for mt in find_mako_tasks(related.setup_tasks):
                run, last = await mt.should_run_task(related, dependency_last_run=last, ainjector=ainjector)
                if run:
                    return False
                if last > last_run:
                    return False
            return True
    return install
