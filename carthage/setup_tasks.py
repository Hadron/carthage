# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import asyncio, dataclasses, logging, os, os.path, typing, weakref
import carthage
from carthage.dependency_injection import AsyncInjector
from carthage.config import ConfigLayout

logger = logging.getLogger('carthage')

_task_order = 0

def _inc_task_order():
    global _task_order
    t = _task_order
    _task_order += 1
    return t

@dataclasses.dataclass
class TaskWrapper:

    
    func: typing.Callable
    stamp: str
    order: int = dataclasses.field(default_factory = _inc_task_order)
    invalidator_func = None
    check_completed_func = None
    

    def __get__(self, instance, owner):
        if instance is None: return self
        return TaskMethod(self, instance)
    
    def __getattr__(self, a):
        if a == "func": raise AttributeError
        return getattr(self.func, a)

    def __setattr__(self, a, v):
        if a in ('func', 'stamp', 'order',
                 'invalidator_func', 'check_completed_func'):
            return super().__setattr__(a, v)
        else:
            return setattr(self.func, a, v)

    def __call__(self, instance, *args, **kwargs):
        try:
            res =  self.func( instance, *args, **kwargs)
            if not self.check_completed_func:
                create_stamp(instance.stamp_path, self.stamp)
            return res
        except SkipSetupTask:
            raise
        except Exception:
            if not self.check_completed_func:
                logger_for(instance).warning(f'Deleting {self.stamp} task stamp for {instance} because task failed')
                delete_stamp(instance.stamp_path, self.stamp)
            raise
            
    @property
    def __wraps__(self):
        return self.func

    async def should_run_task(self, obj: SetupTaskMixin, ainjector:AsyncInjector):

        ''' 
        Return true if this task should be run.

        * If :meth:`check_completed` has been called, then the task should be run when either the check_completed function returns falsy or our dependencies have been run more recently.

        * Otherwise, if there is no stamp then this task should run

        * If there is a :meth:`invalidator`, then this task should run if the invalidator returns falsy.

        * This task should run if any dependencies have run more recently than the stamp

        * Otherwise this task should not run.

        :param: obj
            The instance on which setup_tasks are being run.

        '''
        if self.check_completed_func:
            return await ainjector(self.check_completed_func, obj)
        stamp_time = check_stamp(obj.stamp_path, self.stamp)
        if not stamp_time:
            return True
        if self.invalidator_func:
            if not await ainjector(self.invalidator_func, obj):
                return True
        return False
    

    def invalidator(self, slow = False):
        '''
        Decorator to indicate  an invalidation function for a :func:`setup_task`

        This decorator indicates a function that will validate whether some setup_task has successfully been created.  As an example, if a setup_task creates a VM, an invalidator could invalidate the task if the VM no longer exists.  Invalidators work as an additional check along side the existing mechanisms to track which setup_tasks are run.  Even if an invalidator  would not invalidate a task, the task would still be performed if its stamp does not exist.  Compare :meth:`check_completed` for a mechanism to exert direct control over whether a task is run.

        :param: slow
            If true, this invalidator is slow to run and should only be run if ``config.expensive_checks`` is True.

        Invalidators should return something True if the task is valid and something falsy to invalidate the task and request that the task and all dependencies be re-run.

        Usage example::

            @setup_task("create_vm)
            async def create_vm(self):
                # ...
            @create_vm.invalidator()
            async def create_vm(self):
                # if VM exists return true else false

        '''
        def wrap(f):
            self.invalidator_func = f
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

class TaskMethod:

    def __init__(self, task, instance):
        self.task = task
        self.instance = weakref.proxy(instance)

    def __call__(self, *args, **kwargs):
        self.task(self.instance, *args, **kwargs)

    def __getattr__(self, a):
        return getattr(self.task.func, a)
    
    def __repr__(self):
        return f"<TaskMethod {self.task.stamp} of {self.instance}>"
    

    
    
def setup_task(stamp):
    '''Mark a method as a setup task.  Indicate a stamp file to be created
    when the operation succeeds.  Must be in a class that is a subclass of
    SetupTaskMixin.  Usage:

        @setup_task("unpack"
        async def unpack(self): ...
    '''
    def wrap(fn):
        global _task_order
        t = TaskWrapper(func = fn, stamp = stamp, order = _task_order)
        _task_order += 1
        return t
    return wrap

class SkipSetupTask(Exception): pass

class SetupTaskMixin:

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_tasks = sorted(self._class_setup_tasks(),
                                  key = lambda t: t.order)

    def add_setup_task(self, stamp, task):
        self.setup_tasks.append(TaskWrapper(func = task, stamp = stamp))

    async def run_setup_tasks(self, context = None):
        '''Run the set of collected setup tasks.  If context is provided, it
        is used as an asynchronous context manager that will be entered before the
        first task and eventually exited.  The context is never
        entered if no tasks are run.
        '''
        injector = getattr(self, 'injector', carthage.base_injector)
        ainjector = getattr(self, 'ainjector', None)
        if ainjector is None:
            ainjector = injector(AsyncInjector)
        config = getattr(self, 'config_layout', None)
        if config is None:
            config = injector(ConfigLayout)
        context_entered = False
        for t in self.setup_tasks:
            if await t.should_run_task(self, ainjector):
                try:
                    if (not context_entered) and context is not None:
                        await context.__aenter__()
                        context_entered = True
                    logger_for(self).info(f"Running {t.stamp} task for {self}")
                    await ainjector(t, self)
                except SkipSetupTask: pass
                except Exception:
                    logger_for(self).exception( f"Error running {t.stamp} for {self}:")
                    if context_entered:
                        await context.__aexit__(*sys.exc_info())
                    raise
            else: #should_run_task
                logger_for(self).debug(f"Task {t.stamp} already run for {self}")
        if context_entered:
            await context.__aexit__(None, None, None)

    def _class_setup_tasks(self):
        cls = self.__class__
        meth_names = {}
        for c in cls.__mro__:
            if not issubclass(c, SetupTaskMixin): continue
            for m in c.__dict__:
                if m in meth_names: continue
                meth = getattr(c, m)
                meth_names[m] = True
                if isinstance(meth, TaskWrapper):
                    yield meth

    async def async_ready(self):
        '''
This may need to be overridden, but is provided as a default
'''
        await self.run_setup_tasks()
        return self
    

def create_stamp(path, stamp):
    try:
        with open(os.path.join(path, ".stamp-"+stamp), "w") as f:
            pass
    except FileNotFoundError:
        os.makedirs(path, exist_ok = True)
        with open(os.path.join(path, ".stamp-"+stamp), "w") as f:
            pass

def delete_stamp(path, stamp):
    try:
        os.unlink(os.path.join(path, ".stamp-"+stamp))
    except FileNotFoundError: pass
    


def check_stamp(path, stamp, raise_on_error = False):
    if not os.path.exists(os.path.join(path,
                                       ".stamp-"+stamp)):
        if raise_on_error: raise RuntimeError("Stamp not available")
        return False
    return True

def logger_for(instance):
    try:
        return instance.logger
    except AttributeError: return logger
    
