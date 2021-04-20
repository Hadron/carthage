# Copyright (C) 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import asyncio, dataclasses, datetime, logging, os, os.path, time, typing, sys, shutil, weakref
import importlib.resources
from pathlib import Path
import carthage
from carthage.dependency_injection import AsyncInjector, inject
from carthage.config import ConfigLayout
from carthage.utils import memoproperty
import collections.abc

__all__ = [ 'logger', 'TaskWrapper', 'TaskMethod', 'setup_task', 'SkipSetupTask', 'SetupTaskMixin',
            'mako_task',
            "install_mako_task"]

logger = logging.getLogger('carthage.setup_tasks')

_task_order = 0

def _inc_task_order():
    global _task_order
    t = _task_order
    _task_order += 100
    return t

@dataclasses.dataclass
class TaskWrapper:

    
    func: typing.Callable
    description: str
    order: int = dataclasses.field(default_factory = _inc_task_order)
    invalidator_func = None
    check_completed_func = None

    @memoproperty
    def stamp(self):
        return self.func.__name__

    def __set_name__(self, owner, name):
        self.stamp = name
    def __get__(self, instance, owner):
        if instance is None: return self
        return TaskMethod(self, instance)
    
    def __getattr__(self, a):
        if a == "func": raise AttributeError
        return getattr(self.func, a)

    extra_attributes = frozenset()

    def __setattr__(self, a, v):
        if a in ('func', 'stamp', 'order',
                 'invalidator_func', 'check_completed_func') or a in self.__class__.extra_attributes:
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

    async def should_run_task(self, obj: SetupTaskMixin, 
                              dependency_last_run: float = None,
                              *, ainjector:AsyncInjector):


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
            if not await ainjector(self.invalidator_func, obj, last_run = last_run):
                logger.info(f"Task {self.description} invalidated for {obj}; last run {_iso_time(last_run)}")
                return (True, time.time())
        return (False, last_run)
    

    def invalidator(self, slow = False):
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
    

    
    
def setup_task(description, *,
               order = None,
               before = None):
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
        order = before.order-1
    if order and order > _task_order:
        _task_order = order
        _inc_task_order()

    def wrap(fn):
        kws = {}
        if order: kws['order'] = order
        t = TaskWrapper(func = fn, description = description, **kws)
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
            should_run, dependency_last_run = await t.should_run_task(self,  dependency_last_run, ainjector = ainjector)
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
        return await super().async_ready()

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

class cross_object_dependency(TaskWrapper):

    '''
    Usage::

        # in a client machine's class
        fileserver_dependency = cross_object_dependency(FileServer.update_files, 'fileserver')

    :param task: a :class:`TaskWrapper`, typically associated with another class.

    :param relationship: The string name of a relationship such that calling the *relationship* method on an instance containing this dependency will yield the instance containing *task* that we want to depend on.

    '''

    dependent_task: TaskWrapper
    relationship: str

    def __init__(self, task, relationship, **kwargs):
        super().__init__(func = lambda self: None,
                         description = f'Dependency on `{task.description}\' task of {relationship}',
                         **kwargs)
        self.dependent_task = task
        self.relationship = relationship

    @inject(ainjector = AsyncInjector)
    async def check_completed_func(self, instance, ainjector):
        task = self.dependent_task
        should_run, last_run  = await task.should_run_task( getattr(instance, self.relationship), ainjector = ainjector)
        # We don't care about whether the task would run again, only when it last run.
        if last_run >0.0: return last_run
        #We have no last_run info so we don't know that we need to trigger a re-run
        return True


    def __repr__(self):
        return f'<Depend on {self.dependent_task.description} task of {self.relationship}>'
    

class mako_task(TaskWrapper):

    template: str
    output: str

    extra_attributes = frozenset({'template', 'output',
                                  })

    def __init__(self, template, output = None, **kwargs):
        injections = getattr(self, '_injection_dependencies', {})
        #A separate function so that injection works; consider
        #TaskMethod.__setattr__ to understand.
        def func(*args, **kwargs):
            return self.render(*args, **kwargs)
        self.template = template
        if output is None:
            output = template
            if output.endswith('.mako'): output = output[:-5]
        self.output = output
        super().__init__(func = func, 
                         description = f'Render {self.template} template',
                         **kwargs)

    def __set_name__(self, owner, name):
        super().__set_name__(owner, name)
        import sys, mako.lookup
        module = sys.modules[owner.__module__]
        try: self.lookup = module._mako_lookup
        except AttributeError:
            if hasattr(module, '__path__'): resources= importlib.resources.files(module)
            elif module.__package__ == "":
                resources = Path(module.__file__).parent
            else:
                resources = importlib.resources.files(module.__package__)
            templates = resources/'templates'
            if not templates.exists(): templates = resources
            module._mako_lookup = mako.lookup.TemplateLookup([str(templates)], strict_undefined = True)
            self.lookup = module._mako_lookup


    def render(task, instance, **kwargs):
        template = task.lookup.get_template(task.template)
        output = Path(instance.stamp_path).joinpath(task.output)
        os.makedirs(output.parent, exist_ok = True)
        with open(output, "wt") as f:
            f.write(template.render(
                instance = instance,
                                    **kwargs))

def find_mako_tasks(tasks):
    for t in tasks:
        if isinstance(t, mako_task): yield t
        
def install_mako_task(relationship, cross_dependency = True):

    '''
:param relationship: The name of an attribute property containing :class:`mako_tasks <mako_task>` in its :meth:`~SetupTaskMixin.setup_tasks`.

    :param cross_dependency: If true (the default), rerun the installation whenever any of the underlying mako_tasks change.

    This task is generally associated on a machine to install mako templates rendered on the model.  Typical usage might look like::

        install_mako = install_mako_task('model')

    '''
    @setup_task("Install mako templates")
    async def install(self):
        async with self.filesystem_access() as fspath:
            related = getattr(self, relationship)
            base = Path(related.stamp_path)
            path = Path(fspath)
            for mt in find_mako_tasks(related.setup_tasks):
                if os.path.isabs(mt.output):
                    logger.warn(f'{mt} has absolute path; skipping install')
                    continue
                src = base/mt.output
                dest = path/mt.output
                os.makedirs(dest.parent, exist_ok = True)
                shutil.copy2(src, dest)
    if cross_dependency:
        @install.invalidator()
        @inject(ainjector = AsyncInjector)
        async def install(self, ainjector, last_run, **kwargs):
            related = getattr(self, relationship)
            last = 0.0
            for mt in find_mako_tasks(related.setup_tasks):
                run, last = await mt.should_run_task(related, dependency_last_run = last, ainjector = ainjector)
                if run: return False
                if last > last_run: return False
            return True
    return install
