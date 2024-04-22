#!/usr/bin/python3
# Copyright (C) 2019, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


import asyncio
import argparse
import code
import collections.abc
import concurrent.futures
import functools
import shlex
import re
import time
import pkg_resources
import os.path
import readline
import rlcompleter
import sys
import traceback
import carthage
import carthage.utils
from carthage import base_injector, ConfigLayout
from carthage.dependency_injection import *

__all__ = []

subcommands_re = re.compile(r'^\s*!\s*(\S.*)\s*$')


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
                            dest='console', default=True,
                            help="Do not run the console")
        parser.add_argument('--console', action='store_true',
                            dest='console', default=True,
                            help="Run the console")
        parser.add_argument('--rcfile',
                            metavar="file",
                            default="~/.carthagerc",
                            help="Python code to run in Carthage console")

    def process_arguments(self, args):
        if args.rcfile:
            try:
                with open(os.path.expanduser(args.rcfile), "rt") as f:
                    exec(f.read(), self.locals)
            except FileNotFoundError:
                pass

    @staticmethod
    def noop():
        # dummy function used to start loop when called from another thread
        pass

    @staticmethod
    def default_locals():
        from carthage.dependency_injection.introspection import instantiation_roots
        return {
            'injector': base_injector,
            'instantiation_roots': instantiation_roots,
            'ainjector': base_injector(AsyncInjector),
            'loop': asyncio.get_event_loop(),
            'attach_monitor': attach_event_monitor_to_console,
            'config': base_injector(ConfigLayout)
        }

    def exec_resource(self, pkg, resource):
        res_str = pkg_resources.resource_string(pkg, resource)
        exec(compile(res_str, resource, mode="exec"), self.locals)

    def __init__(self, locals=None, extra_locals=None,
                 history_file="~/.carthage_history"):
        if locals is None:
            locals = self.default_locals()
        if extra_locals is not None:
            locals = {**locals, **extra_locals}
        super().__init__(locals=locals)
        self.locals = locals
        self.orig_completer = None
        self.orig_displayhook = None
        self.completed_keys = []
        self.history = {}
        self.history_num = 0
        self.loop = asyncio.get_event_loop()
        if 'h' in self.locals and self.locals['h'] is not self.history:
            raise NotImplementedError(
                "When replacing `h', it is unclear whether you want to link to the async history object or disable it.")
        self.locals['h'] = self.history
        self.subcommands = None
        self.subcommands_parser = None
        if history_file:
            history_file = os.path.expanduser(history_file)
            try:
                readline.read_history_file(history_file)
            except FileNotFoundError:
                pass
        self.history_file = history_file

    def interact(self, *args, **kwargs):
        kwargs  = dict(kwargs)
        if self.subcommands and 'banner' not in kwargs:
            kwargs['banner'] = "Carthage console.  Type Python expressions or use !help for Carthage commands."
        try:
            asyncio.get_event_loop()
        except Exception:  # Running in thread without loop
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
            if self.history_file:
                readline.write_history_file(self.history_file)

    def raw_input(self, *args, **kwargs):
        for k in self.completed_keys:
            print(f'[{k}]: {self.history[k]}')
        self.completed_keys.clear()
        return super().raw_input(*args, **kwargs)

    def displayhook(self, obj):

        def future_callback(num, f):
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
            future = asyncio.run_coroutine_threadsafe(obj, loop=self.loop)
            future.add_done_callback(functools.partial(future_callback, num))
            print(f'[{num}]: async {obj.__name__}')
            self.history[num] = future
            self.loop.call_soon_threadsafe(CarthageConsole.noop)
        else:
            self.orig_displayhook(obj)

    async def enable_console_commands(self, ainjector):
        parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
        subparser_action = parser.add_subparsers(title='command', dest='cmd', required=True)
        subparser_action.add_parser('help', help='Print Help')
        self.subcommands = await self.setup_subcommands(ainjector, subparser_action)
        self.ainjector = ainjector
        self.subcommands_parser = parser

    async def setup_subcommands(self, ainjector, subparser_action, debug=False):
        '''
        Search through the injector for :class:`CarthageRunnerCommand` instances.  If they are available, then attach them to the parser.

        :return: Mapping of subcommand tag to subcommand instances.

        It is typical for this method to be called more than once.  For example the runner calls this both for its main parser and for a parser used within the console.
        '''
        subcommands = {}
        k = None
        v = None
        for k in ainjector.filter(
                carthage.console.CarthageRunnerCommand,
                ['name']):
            if debug:
                try:
                    v = await ainjector.get_instance_async(k)
                except InjectionFailed:
                    continue
            else:
                with injection_failed_unlogged():
                    try:
                        v = await ainjector.get_instance_async(k)
                    except InjectionFailed:
                        continue

            # Unfortunately depending on when commands are registered
            # by plugins, they are likely registered before the layout
            # ainjector is established. Explicitly override the
            # ainjector in the command so that it can access layout
            # objects.
            v.injector = ainjector.injector
            v.ainjector = ainjector
            if await v.should_register():
                v.register(subparser_action)
            subcommands[v.name] = v
        return subcommands

    def runsource(self, source, filename):
        if self.subcommands:
            match = subcommands_re.match(source)
            if match:
                try:
                    args = self.subcommands_parser.parse_args(shlex.split(match.group(1)))
                except argparse.ArgumentError as e:
                    print(e)
                    return
                if args.cmd == 'help':
                    self.subcommands_parser.print_help()
                    return
                subcommand = self.subcommands[args.cmd]
                if args.help:
                    subcommand.subparser.print_help()
                    return
                future = asyncio.run_coroutine_threadsafe(self.ainjector(subcommand.run, args), loop=self.loop)
                try: future.result()  #concurrent not asyncio future
                except Exception as e:
                    traceback.print_exception(e)
                return
        return super().runsource(source, filename)

    async def setup_from_plugins(self, injector=None):
        from .plugins import CarthagePlugin
        if injector is None:
            injector = base_injector
        for k, plugin in injector.filter_instantiate(CarthagePlugin, ['name']):
            if 'console_setup' in plugin.metadata:
                try:
                    exec(plugin.metadata['console_setup'], self.locals)
                    if 'async_setup' in self.locals:
                        await self.locals['async_setup']()
                except Exception:
                    print('failed to setup plugin ' + plugin.name)
                    traceback.print_exc()
                finally:
                    try:
                        del self.locals['async_setup']
                    except KeyError:
                        pass



__all__ += ['CarthageConsole']


class CarthageRunnerCommand(AsyncInjectable):

    @property
    def name(self):
        raise NotImplementedError('You must set name in a subclass')

    #: Extra arguments to be passed into .add_subparser
    subparser_kwargs = {}

    #: Does generate need to be run on the layout before this command?
    generate_required = False

    def setup_subparser(self, subparser):
        '''Generally calls add_argument a lot.'''
        raise NotImplementedError

    async def run(self, args):
        '''Called when this subcommand is selected.'''
        pass

    async def should_register(self):
        ''' Returns True if this command should be registered.  For example, start_machine might wish to confirm that the layout has Machines before registering.  This function should bd work even if for example there is no CarthageLayout.
'''
        return True

    def register(self, subparser_action):
        self.subparser = subparser_action.add_parser(
            self.name, add_help=False,
            **self.subparser_kwargs)
        self.setup_subparser(self.subparser)
        self.subparser.add_argument('--help', action='store_true', help='Print Usage')

    @classmethod
    def default_class_injection_key(cls):
        return InjectionKey(CarthageRunnerCommand, name=cls.name)


__all__ += ['CarthageRunnerCommand']


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
