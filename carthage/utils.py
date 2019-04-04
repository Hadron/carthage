import argparse, asyncio, functools, logging, weakref

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
        if instance is None: return self
        #Because we don't define set or del, we should not be called
        #if name is already set on instance.  So if we set name we
        #will be bypassed in the future
        res = self.fun(instance)
        setattr(instance, self.name, res)
        return res
    
def when_needed(wraps, *args, injector = None,
                addl_keys = [],
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
    from .dependency_injection import inject, AsyncInjectable, AsyncInjector, InjectionKey, Injectable, _call_close
    # We do not copy the wrapped function's dependencies out.  We will
    # submit the wrapped object to an injector as part of resolving it
    # and we may need to control which injector is used for the
    # dependencies.
    @inject(ainjector = AsyncInjector)
    @functools.wraps(wraps,
                     assigned = functools.WRAPPER_ASSIGNMENTS ,
                     updated = tuple())
    class WhenNeeded(AsyncInjectable):

        resolved_obj = None
        resolving = None

        def __init__(self, *inside_args,  ainjector, **inside_kwargs):
            nonlocal args
            if args and inside_args:
                raise RuntimeError("It does not make sense to specify args both in the call to when_needed and when it is resolved.")
            if inside_args:
                args = inside_args
            if injector is not None:
                #override ainjector
                ainjector = injector(AsyncInjector)
            self.ainjector = ainjector
            self.inside_kwargs = inside_kwargs

        @classmethod
        def supplementary_injection_keys(self, k):
            if isinstance(wraps, type) and issubclass(wraps, Injectable):
                yield from wraps.supplementary_injection_keys(k)
            yield from addl_keys

        @classmethod
        def close(self, canceled_futures = None):
            if self.resolved_obj:
                return _call_close(self.resolved_obj, canceled_futures)
            if self.resolving:
                self.resolving.cancel()
                if canceled_futures: canceled_futures.append(self.resolving)
                self.resolving = None
                
        async def async_ready(self):
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
                del self.ainjector
                del self.inside_kwargs
                return res
            except Exception as e:
                self.resolving.set_exception(e)
                self.__class__.resolving = None # try again next time
                raise
                
        def __repr__(self):
            if isinstance(wraps, type):
                wraps_repr = wraps.__name__
            else: wraps_repr = repr(wraps)
            s = "when_needed({}(".format(wraps_repr)
            for a in args:
                s +=", {}".format(a)
            for k,v in kwargs.items():
                s += ", {}={}".format(k, v)
            s +=")"
            if injector is not None:
                s += ", injector ={}".format(repr(injector))
            s += ")"
            return s

    addl_keys = list(map(
        lambda k: k if isinstance(k, InjectionKey) else InjectionKey(k), addl_keys))
    
    return WhenNeeded

def permute_identifier(id, maxlen):
    "Add to or replace the last character of the identifier; use as generator and stop consuming when a unique one is found"
    yield id
    if len(id) < maxlen:
        for i in range(10):
            yield id+chr(97+i)
    else:
        id = id[:-1]
        for i in range(10):
            yield id+chr(97+i)
    raise ValueError("No unique combination found")



def add_carthage_arguments(parser):
    parser.add_argument('--config',
                        metavar = "file",
                        default = [],
                        type = argparse.FileType('rt'),
                        action = 'append')
    parser.add_argument('--command-verbose',
                        help = "Verbose command logging",
                        action ='store_true')
    parser.add_argument('--tasks-verbose',
                        help = "Verbose logging for tasks",
                        action = 'store_true')
    return parser

def carthage_main_argparser(*args, **kwargs):
    parser = argparse.ArgumentParser(*args, **kwargs)
    add_carthage_arguments(parser)
    return parser

def carthage_main_setup(parser=None):
    from . import base_injector, ConfigLayout
    if parser is None:
        parser = carthage_main_argparser()
    args = parser.parse_args()
    if len(args.config) > 0:
        config = base_injector(ConfigLayout)
        for f in args.config:
            config.load_yaml(f)
    root_logger = logging.getLogger()
    console_handler = logging.StreamHandler()
    root_logger.addHandler(console_handler)
    root_logger.setLevel('INFO')
    container_logger = logging.getLogger('carthage.container')
    container_logger.addHandler(logging.FileHandler('container.log'))
    container_logger.setLevel(10)
    def container_debug_filter(record):
        if record.name == 'carthage.container' and record.levelno == 10: return 0
        return 1
    console_handler.addFilter(container_debug_filter)
    if not args.command_verbose:
        logging.getLogger('carthage.sh').setLevel(logging.ERROR)
        logging.getLogger('carthage.sh').propagate = False
    if args.tasks_verbose:
        logging.getLogger('carthage.setup_tasks').setLevel(10)
        
    return args

def carthage_main_run(func, *args, **kwargs):
    loop = asyncio.get_event_loop()
    from . import base_injector, AsyncInjector, shutdown_injector
    from .config import inject_config
    inject_config(base_injector)
    ainjector = base_injector(AsyncInjector)
    try:
        loop.run_until_complete(ainjector(func, *args, **kwargs))
    finally:
        loop.run_until_complete(shutdown_injector(base_injector))
        


__all__ = ['when_needed', 'possibly_async', 'permute_identifier', 'memoproperty',
           'add_carthage_arguments', 'carthage_main_argparser',
           'carthage_main_setup', 'carthage_main_run']
