# Copyright (C) 2018, 2019, 2020, 2021, 2022, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import argparse
import asyncio
import contextlib
import fcntl
import functools
import logging
import os
import posix
import pathlib
import re
import typing
import weakref
import importlib.resources
import mako.lookup


async def possibly_async(r):
    '''If r is a coroutine, await it.  Otherwise return it.  Used like the
    following:

        return await possibly_async(self.check_volume())

    check_volume can now optionally be declared async
    '''
    if asyncio.iscoroutine(r):
        return await r
    else:
        return r


class memoproperty:
    "A property that only supports getting and that stores the result the first time on the instance to avoid recomputation"

    def __init__(self, fun):
        functools.update_wrapper(self, fun)
        self.fun = fun
        self.name = fun.__name__

    def __get__(self, instance, owner):
        if instance is None:
            return self
        # Because we don't define set or del, we should not be called
        # if name is already set on instance.  So if we set name we
        # will be bypassed in the future
        res = self.fun(instance)
        setattr(instance, self.name, res)
        return res


class WhenNeededMeta(type):

    def __repr__(self):
        if self.resolved_obj:
            return f'<when_needed resolved={repr(self.resolved_obj)}>'

        wraps, args, kwargs, injector = self.repr_info
        if isinstance(wraps, type):
            wraps_repr = wraps.__name__
        else:
            wraps_repr = repr(wraps)
        s = "when_needed({}".format(wraps_repr)
        for a in args:
            s += ", {}".format(a)
        for k, v in kwargs.items():
            s += ", {}={}".format(k, v)
        s += ")"
        if injector is not None:
            s += ", injector ={}".format(repr(injector))
            s += ")"
        return s


def when_needed(wraps, *args, injector=None,
                addl_keys=[],
                **kwargs):
    '''Return an AsyncInjectable class that when resolved will instantiate
    'wraps' giving it *args and **kwargs.  This is only done once; if this
    class is instantiated more than once the same shared object is
    returned.  By default, the injector used when we are instantiated will
    be used, but if the injector keyword is passed in then that injector
    will always be used for the instantiation.  Differences between this
    and passing a type into add_provider are:

    * If the return from this function is passed into add_provider for
      different injection keys, then these objects will be the same
      when instantiated.  Without when_needed, the keys associated
      with each call to add_provider would instantiate different
      objects of the same type.

    * This may be used to instantiate objects that require
      non-injected configuration to their constructors.

    '''
    from .dependency_injection import inject, AsyncInjectable, AsyncInjector, InjectionKey, Injectable, _call_close, Injector
    # We do not copy the wrapped function's dependencies out.  We will
    # submit the wrapped object to an injector as part of resolving it
    # and we may need to control which injector is used for the
    # dependencies.
    # We override the injector dependency to avoid autokwargs behavior because we do not want our injector claimed

    @inject(injector=Injector)
    @functools.wraps(wraps,
                     assigned=functools.WRAPPER_ASSIGNMENTS,
                     updated=tuple())
    class WhenNeeded(AsyncInjectable, metaclass=WhenNeededMeta):

        resolved_obj = None
        resolving = None

        def __init__(self, *inside_args, **inside_kwargs):
            nonlocal args
            if args and inside_args:
                raise RuntimeError(
                    "It does not make sense to specify args both in the call to when_needed and when it is resolved.")
            if inside_args:
                args = inside_args
            if injector is not None:
                # override injector
                inside_kwargs['injector'] = injector
            # We want to end up  with a copied injector but not one that is claimed so
            # the resulting object can claim it.
            self.injector = inside_kwargs.pop('injector')
            self.injector = self.injector(type(self.injector))
# super will set up ainjector
            super().__init__()
            self.ainjector_set.add(self.ainjector)
            self.inside_kwargs = inside_kwargs

        @classmethod
        def supplementary_injection_keys(self, k):
            if isinstance(wraps, type) and issubclass(wraps, Injectable):
                yield from wraps.supplementary_injection_keys(k)
            yield from addl_keys

        @classmethod
        def close(self, canceled_futures=None):
            if self.resolved_obj:
                _call_close(self.resolved_obj, canceled_futures)
            for ainjector in self.ainjector_set:
                ainjector.close(canceled_futures=canceled_futures)
            self.ainjector_set.clear()
            if hasattr(self, 'resolving') and self.resolving:
                self.resolving.cancel()
                if canceled_futures:
                    canceled_futures.append(self.resolving)
                self.resolving = None

        # class level property to track all the injectors in use.
        ainjector_set = weakref.WeakSet()
        repr_info = (wraps, args, kwargs, injector)

        async def async_resolve(self):
            nonlocal kwargs
            if self.resolved_obj:
                return self.resolved_obj
            if self.resolving:
                return await asyncio.shield(self.resolving)
            loop = self.ainjector.get_instance(asyncio.AbstractEventLoop)
            self.__class__.resolving = loop.create_future()
            del loop
            kws = kwargs.copy()
            kws.update(self.inside_kwargs)
            try:
                res = await self.ainjector(wraps, *args, **kws)
                self.__class__.resolved_obj = res
                self.resolving.set_result(res)
                del self.__class__.resolving
                # We will never need them again so release the references
                kwargs = None
                del self.__class__.repr_info
                del self.ainjector
                del self.inside_kwargs
                return res
            except Exception as e:
                self.resolving.set_exception(e)
                self.__class__.resolving = None  # try again next time
                raise

    addl_keys = list(map(
        lambda k: k if isinstance(k, InjectionKey) else InjectionKey(k), addl_keys))

    return WhenNeeded


def permute_identifier(id, maxlen):
    "Add to or replace the last character of the identifier; use as generator and stop consuming when a unique one is found"
    yield id
    if len(id) < maxlen:
        for i in range(10):
            yield id + chr(97 + i)
    else:
        id = id[:-1]
        for i in range(10):
            yield id + chr(97 + i)
    raise ValueError("No unique combination found")


def add_carthage_arguments(parser):
    parser.add_argument('--config',
                        metavar="file",
                        default=[],
                        type=argparse.FileType('rt'),
                        action='append')
    parser.add_argument('--command-verbose',
                        help="Verbose command logging",
                        action=argparse.BooleanOptionalAction)
    parser.add_argument('--tasks-verbose',
                        help="Verbose logging for tasks",
                        action=argparse.BooleanOptionalAction)
    parser.add_argument('--plugin',
                        dest='plugins',
                        default=[],
                        action='append',
                        help='Load a plugin into Carthage',
                        metavar='plugin')
    parser.add_argument('--no-default-config',
                        dest='default_config',
                        action='store_false',
                        default=True,
                        help='Disable reading /etc/carthage_system.conf as root and ~/.carthage.conf for other users.'
                        )
    
    return parser


def carthage_main_argparser(*args, **kwargs):
    parser = argparse.ArgumentParser(*args, **kwargs)
    add_carthage_arguments(parser)
    return parser


def carthage_main_setup(parser=None, unknown_ok=False, ignore_import_errors=False):
    from . import base_injector, ConfigLayout
    from .plugins import load_plugin
    if parser is None:
        parser = carthage_main_argparser()
    if unknown_ok:
        result = parser.parse_known_args()
        args = result[0]
    else:
        args = parser.parse_args()
        result = args
    root_logger = logging.getLogger()
    console_handler = logging.StreamHandler()
    root_logger.addHandler(console_handler)
    root_logger.setLevel('INFO')
    container_logger = logging.getLogger('carthage.container')
    container_logger.addHandler(logging.FileHandler('container.log', delay=True))
    container_logger.setLevel(10)

    def container_debug_filter(record):
        if record.name == 'carthage.container' and record.levelno == 10:
            return 0
        return 1
    console_handler.addFilter(container_debug_filter)

    config = base_injector(ConfigLayout)
    if args.default_config:
        load_default_config(config)
    for f in args.config:
        config.load_yaml(f, ignore_import_errors=ignore_import_errors)
    for p in args.plugins:
        base_injector(load_plugin, p, ignore_import_errors=ignore_import_errors)
    if not args.command_verbose:
        logging.getLogger('sh').setLevel(logging.ERROR)
        logging.getLogger('carthage.sh').propagate = False
        logging.getLogger('urllib3.connectionpool').setLevel(logging.ERROR)
        logging.getLogger('urllib3.connectionpool').propagate = False
    if args.tasks_verbose:
        logging.getLogger('carthage.setup_tasks').setLevel(10)

    return result


def carthage_main_run(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    from . import base_injector, AsyncInjector, shutdown_injector
    from .config import inject_config
    inject_config(base_injector)
    ainjector = base_injector(AsyncInjector)
    try:
        return loop.run_until_complete(ainjector(func, *args, **kwargs))
    finally:
        loop.run_until_complete(shutdown_injector(base_injector))

def load_default_config(config):
    is_root_uid = (posix.geteuid() == 0)
    if is_root_uid or os.environ.get('USER') == 'root':
        config_file = '/etc/carthage_system.conf'
    else:
        config_file = os.path.expanduser('~/.carthage.conf')
    config_path = pathlib.Path(config_file)
    if config_path.exists():
        with config_path.open('rt') as f:
            config.load_yaml(f)
            

shell_safe_re = re.compile(r'^[a-zA-Z0-9_./ ]*$')


def validate_shell_safe(s):
    if shell_safe_re.search(s):
        return True
    return False


@contextlib.contextmanager
def TemporaryMountPoint(**kwargs):
    '''
    Create a tempory directory to be used as a mount point.  The directory name is the value of the context.
    If the directory is empty, it is deleted when the context is exited.

    The advantage of this function over :class:`tempfile.TemporaryDirectory` for mount points is that if somehow the unmount fails, the mounted filesystem's contents are not deleted.
'''
    dir = tempfile.mkdtemp(**kwargs)
    try:
        yield dir
    finally:
        os.rmdir(dir)


def import_resources_files(package):
    "stub for importlib.resources.files"
    try:
        return importlib.resources.files(package)
    except AttributeError:
        if isinstance(package, str):
            return pathlib.Path(importlib.import_module(package).__path__[0])
        else:
            return pathlib.Path(package.__path__[0])


mako_lookup = mako.lookup.TemplateLookup([import_resources_files(__package__) / "resources/templates"],
                                         strict_undefined=True)


def is_optional_type(t):
    # support both python 3.7 and python 3.9
    # As of python 3.9 there is not a way to do this without using internals
    try:
        if t.__class__ == typing._UnionGenericAlias:
            t = typing.get_args(t)
            if type(None) in t:
                return True
    except AttributeError:
        if t.__class__ is typing._GenericAlias:
            if type(None) in t.__args__:
                return True
    return False


def get_type_args(t):
    try:
        return typing.get_type_args(t)
    except AttributeError:
        return t.__args__


@contextlib.asynccontextmanager
async def file_locked(fd: typing.Union[int, str], mode=fcntl.LOCK_EX, unlock=False):
    loop = asyncio.get_event_loop()
    close = False
    if isinstance(fd, str) or hasattr(fd, "__fspath__"):
        fd = os.open(fd, os.O_CREAT | os.O_CLOEXEC | os.O_RDWR, 0o664)
        close = True

    def lock(m):
        fcntl.lockf(fd, m)
    await loop.run_in_executor(None, lock, mode)
    try:
        yield
    finally:
        if unlock:
            await loop.run_in_executor(None, lock, fcntl.LOCK_UN)
        if close:
            os.close(fd)


class NotPresentType:
    '''A singleton value indicating that an dependency should not be injected into a function if if the dependency is not provided.  Used as the value for the *_optional* parameter to :func:`~carthage.dependency_injection.inject`
    Usage::

        @inject(router=InjectionKey(Router, _optional=NotPresent))
        def func(**kwargs):
            # If the injector does not provide Router, kwargs will not contain 'router'
            #If _optional had been True, then router would be None when not provided.

    '''

    def __new__(cls):
        raise TypeError('NotPresent is a singleton')

    def __repr__(self):
        return 'NotPresent'


NotPresent = object.__new__(NotPresentType)


def relative_path(p):
    '''Returns a :class:`pathlib.Path` that is guaranteed to be relative.  Intended to be used to make sure that joining the return value to a filesystem root does not escape the filesystem.
    '''
    p = pathlib.Path(p)
    if p.is_absolute():
        return p.relative_to('/')
    return p


__all__ = ['when_needed', 'possibly_async', 'permute_identifier', 'memoproperty',
           'add_carthage_arguments', 'carthage_main_argparser',
           'carthage_main_setup', 'carthage_main_run',
           'validate_shell_safe',
           'is_optional_type',
           'TemporaryMountPoint',
           'import_resources_files',
           'mako_lookup',
           'file_locked',
           'relative_path',
           ]
