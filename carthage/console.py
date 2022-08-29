#!/usr/bin/python3
# Copyright (C) 2019, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


import asyncio, argparse, code, collections.abc, time
import pkg_resources, os.path, readline, rlcompleter, sys, traceback
import carthage, carthage.utils
from carthage import base_injector, ConfigLayout
from carthage.dependency_injection import *
__all__ = []




class EventMonitor:

    def __init__(self, injector):
        injector.add_event_listener(InjectionKey(Injector), "dependency_progress", self.progress_cb)
        injector.add_event_listener(InjectionKey(Injector), "dependency_final", self.final_cb)
        self.injector = injector

    def close(self):
        self.injector.remove_event_listener(InjectionKey(Injector), "dependency_progress", self.progress_cb)
        self.remove_event_listener(InjectionKey(Injector), "dependency_final", self.final_cb)

    def progress_cb(self, target, **kwargs):
                print(f'Progress: {target}: {target.provider.provider}')

    def final_cb(self, target, **kwargs):
        print(f'Resolved: {target}: {target.get_value(False)}')

def attach_event_monitor_to_console(injector):
    return EventMonitor(injector)

        
class CarthageConsole(code.InteractiveConsole):

    @staticmethod
    def add_arguments(parser):
        parser.add_argument('--no-console', action='store_false',
                            dest = 'console', default=True,
                            help = "Do not run the console")
        parser.add_argument('--console', action='store_true',
                            dest = 'console', default=True,
                            help = "Run the console")
        parser.add_argument('--rcfile',
                            metavar = "file",
                            default = "~/.carthagerc",
                help = "Python code to run in Carthage console")

    def process_arguments(self, args):
        if args.rcfile:
            try:
                with open(os.path.expanduser(args.rcfile), "rt") as f:
                    exec(f.read(), self.locals)
            except FileNotFoundError: pass

    @staticmethod
    def noop():
        #dummy function used to start loop when called from another thread
        pass

    @staticmethod
    def default_locals():
        class sleeper():
            def __repr__(self):
                time.sleep(2**31)
        from carthage.dependency_injection.introspection import instantiation_roots
        return {
            'injector': base_injector,
            'instantiation_roots': instantiation_roots,
            'ainjector': base_injector(AsyncInjector),
            'sleep': sleeper(),
            'loop': asyncio.get_event_loop(),
            'attach_monitor': attach_event_monitor_to_console,
            'config': base_injector(ConfigLayout)
        }

    def exec_resource(self, pkg, resource):
        res_str = pkg_resources.resource_string(pkg, resource)
        exec(compile(res_str, resource, mode = "exec"),  self.locals)
        
    def __init__(self, locals=None, extra_locals=None,
                 history_file="~/.carthage_history"):
        if locals is None:
           locals = self.default_locals()
        if extra_locals is not None:
            locals = { **locals, **extra_locals }
        super().__init__(locals=locals)
        self.locals = locals
        self.orig_completer = None
        self.orig_displayhook = None
        self.completed_keys = []
        self.history = {}
        self.history_num = 0
        self.loop = asyncio.get_event_loop()
        if 'h' in self.locals and self.locals['h'] is not self.history:
            raise NotImplementedError("When replacing `h', it is unclear whether you want to link to the async history object or disable it.")
        self.locals['h'] = self.history
        if history_file:
            history_file = os.path.expanduser(history_file)
            try: readline.read_history_file(history_file)
            except FileNotFoundError: pass
        self.history_file = history_file

    def interact(self, *args, **kwargs):
        try: asyncio.get_event_loop()
        except Exception: #Running in thread without loop
            asyncio.set_event_loop(self.loop)
        self.orig_completer = readline.get_completer()
        self.orig_displayhook = sys.displayhook

        sys.displayhook = self.displayhook
        completer = rlcompleter.Completer(self.locals)
        readline.set_completer(completer.complete)
        readline.parse_and_bind('tab: complete')

        try:
            super().interact(*args, **kwargs)
        finally:
            readline.set_completer(self.orig_completer)
            sys.displayhook = self.orig_displayhook
            if self.history_file: readline.write_history_file(self.history_file)
            
    def raw_input(self, *args, **kwargs):
        for k in self.completed_keys:
            print(f'[{k}]: {self.history[k]}')
        self.completed_keys.clear()
        return super().raw_input(*args, **kwargs)
    
    def displayhook(self, obj):

        def future_callback(f):
            del self.history[num]
            try:
                self.history[num] = f.result()
                self.completed_keys.append(num)
            except Exception as e:
                print(f'[{num}]-> exception')
                traceback.print_exc()
                
        if isinstance(obj, collections.abc.Coroutine):
            self.history_num += 1
            num = self.history_num
            future = asyncio.ensure_future(obj, loop=self.loop)
            future.add_done_callback(future_callback)
            print(f'[{num}]: async {obj.__name__}')
            self.history[num] = future
            self.loop.call_soon_threadsafe(CarthageConsole.noop)
        else:
            self.orig_displayhook(obj)

__all__ += ['CarthageConsole']

subparser_action_key = InjectionKey('argparse.SubParserAction')

@inject_autokwargs(subparser_action=subparser_action_key,
                   )
class CarthageRunnerCommand(AsyncInjectable):

    #Although this class is an Injectable, because of the way it is
    #instantiated, async_resolve and async_ready must both be trivial.
    #It is an AsyncInjectable only to get a claimed AsyncInjector.
    
    @property
    def name(self):
        raise NotImplementedError('You must set name in a subclass')

    #: Extra arguments to be passed into .add_subparser
    subparser_kwargs = {}

    #: Does generate need to be run on the layout before this command?
    generate_required=False

    def setup_subparser(self, subparser):
        '''Generally calls add_argument a lot.'''
        raise NotImplementedError

    async def run(self, args):
        '''Called when this subcommand is selected.'''
        pass

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        parser = self.subparser_action.add_parser(
            self.name, **self.subparser_kwargs)
        self.setup_subparser(parser)
        
    @classmethod
    def default_class_injection_key(cls):
        return InjectionKey(CarthageRunnerCommand, name=cls.name)


__all__ += ['CarthageRunnerCommand', 'subparser_action_key']
        
        
def main():

    console = CarthageConsole()
    parser = carthage.utils.carthage_main_argparser()
    CarthageConsole.add_arguments(parser)
    args = carthage.utils.carthage_main_setup(parser)
    console.process_arguments(args)
    loop = asyncio.get_event_loop()

    async def run():
        await loop.run_in_executor(None, console.interact)
    carthage.utils.carthage_main_run(run)

if __name__ == '__main__':
    main()
