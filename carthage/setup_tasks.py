from __future__ import annotations
import asyncio, dataclasses, datetime, logging, os, os.path, time, typing, sys, weakref
import carthage
from carthage.dependency_injection import AsyncInjector
from carthage.config import ConfigLayout
import collections.abc

__all__ = [ 'logger', 'TaskWrapper', 'TaskMethod', 'setup_task', 'SkipSetupTask', 'SetupTaskMixin' ]

logger = logging.getLogger('carthage.setup_tasks')

_task_order = 0

def _inc_task_order():
    global _task_order
    t = _task_order
    _task_order += 1
    return t

@dataclasses.dataclass
class TaskWrapper:

    
    func: typing.Callable
    description: str
    order: int = dataclasses.field(default_factory = _inc_task_order)
    invalidator_func = None
    check_completed_func = None

    @property
    def stamp(self):
        return self.func.__name__

    def __set_name(self, owner, name):
        self.stamp = name
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
        def callback(fut):
            try:
                res = fut.result()
                if not self.check_completed_func:
                    instance.create_stamp(self.stamp)
            except SkipSetupTask: pass
            except Exception:
                if not self.check_completed_func:
                    instance.logger_for().warning(f'Deleting {self.description} task stamp for {instance} because task failed')
                    instance.delete_stamp(self.stamp)

        try:
            res = self.func(instance, *args, **kwargs)
            if isinstance(res, collections.abc.Coroutine):
                res = asyncio.ensure_future(res)
                res.add_done_callback(callback)
                if hasattr(instance,'name'):
                    res.purpose = f'setup task: {self.stamp} for {instance.name}'
                return res
            else:
                if not self.check_completed_func:
                    instance.create_stamp(self.stamp)
        except SkipSetupTask:
            raise
        except Exception:
            if not self.check_completed_func:
                instance.logger_for().warning(f'Deleting {self.description} task stamp for {instance} because task failed')
                instance.delete_stamp(self.stamp)
            raise
            
    @property
    def __wraps__(self):
        return self.func

    async def should_run_task(self, obj: SetupTaskMixin, ainjector:AsyncInjector,
                              dependency_last_run: float):


        ''' 
        Indicate whether this task should be run for *obj*.

        :returns: Tuple of whether the task should be run and when the task was last run if ever.

        * If :meth:`check_completed` has been called, then the task should be run when either the check_completed function returns falsy or our dependencies have been run more recently.

        * Otherwise, if there is no stamp then this task should run

        * If there is a :meth:`invalidator`, then this task should run if the invalidator returns falsy.

        * This task should run if any dependencies have run more recently than the stamp

        * Otherwise this task should not run.

        :param: obj
            The instance on which setup_tasks are being run.

        '''
        if dependency_last_run is None:
            dependency_last_run = 0.0
        if self.check_completed_func:
            last_run =  await ainjector(self.check_completed_func, obj)
            if last_run is True:
                logger.debug(f"Task {self.description} for {obj} run without providing timing information")
                return (False, dependency_last_run)
        else: last_run  = obj.check_stamp(self.stamp)
        if last_run is False:
            logger.debug(f"Task {self.description} never run for {obj}")
            return (True, dependency_last_run)
        if last_run < dependency_last_run:
            logger.debug(f"Task {self.description} last run {_iso_time(last_run)}, but dependency run more recently at {_iso_time(dependency_last_run)}")
            return (True, dependency_last_run)
        logger.debug(f"Task {self.description} last run for {obj} at {_iso_time(last_run)}")
        if self.invalidator_func:
            if not await ainjector(self.invalidator_func, obj):
                logger.info(f"Task {self.description} invalidated for {obj}; last run {_iso_time(last_run)}")
                return (True, time.time())
        return (False, last_run)
    

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
        return self.task(self.instance, *args, **kwargs)

    def __getattr__(self, a):
        return getattr(self.task.func, a)
    
    def __repr__(self):
        return f"<TaskMethod {self.task.stamp} of {self.instance}>"
    

    
    
def setup_task(description):
    '''Mark a method as a setup task.  Describe the task for logging.  Must be in a class that is a subclass of
    SetupTaskMixin.  Usage:

        @setup_task("unpack"
        async def unpack(self): ...
    '''
    def wrap(fn):
        global _task_order
        t = TaskWrapper(func = fn, description = description, order = _task_order)
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
        dry_run = config.tasks.dry_run
        dependency_last_run = 0.0
        for t in self.setup_tasks:
            should_run, dependency_last_run = await t.should_run_task(self, ainjector, dependency_last_run)
            if should_run:
                try:
                    if (not context_entered) and context is not None:
                        await context.__aenter__()
                        context_entered = True
                    if not dry_run:
                        self.logger_for().info(f"Running {t.description} task for {self}")
                        await ainjector(t, self)
                        dependency_last_run = time.time()
                    else:
                        self.logger_for().info(f'Would run {t.description} task for {self}')
                except SkipSetupTask: pass
                except Exception:
                    self.logger_for().exception( f"Error running {t.description} for {self}:")
                    if context_entered:
                        await context.__aexit__(*sys.exc_info())
                    raise
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

    def create_stamp(self, stamp):
        try:
            with open(os.path.join(self.stamp_path, ".stamp-"+stamp), "w") as f:
                pass
        except FileNotFoundError:
            os.makedirs(self.stamp_path, exist_ok = True)
            with open(os.path.join(self.stamp_path, ".stamp-"+stamp), "w") as f:
                pass

    def delete_stamp(self, stamp):
        try:
            os.unlink(os.path.join(self.stamp_path, ".stamp-"+stamp))
        except FileNotFoundError: pass

    def check_stamp(self, stamp, raise_on_error = False):
        '''
        :returns: False if the stamp is not present and *raise_on_error* is False else the unix time of the stamp.
        '''
        if raise_on_error not in (True,False):
            raise SyntaxError(f'raise_on_error must be a boolean. current value: {raise_on_error}')
        try:
            res = os.stat(os.path.join(self.stamp_path, ".stamp-"+stamp))
        except FileNotFoundError:
            if raise_on_error: raise RuntimeError(f"stamp directory '{self.stamp_path}' did not exist") from None
            return False
        return res.st_mtime

    def logger_for(self):
        try:
            return self.logger
        except AttributeError: return logger
    

def _iso_time(t):
    return datetime.datetime.fromtimestamp(t).isoformat()
