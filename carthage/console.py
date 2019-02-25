#!/usr/bin/python3

import asyncio, argparse, code, collections.abc, os.path, readline, rlcompleter, sys, traceback
import carthage, carthage.utils
from carthage import base_injector, AsyncInjector, ConfigLayout
import carthage.vmware.vm

class CarthageConsole(code.InteractiveConsole):

    @staticmethod
    def add_arguments(parser):
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

    def __init__(self, locals=None):
        self.locals = locals
        if self.locals is None:
            self.locals = {
                'injector': base_injector,
                'ainjector': base_injector(AsyncInjector),
                'loop': asyncio.get_event_loop(),
                'config': base_injector(ConfigLayout)
            }
        super().__init__(locals=self.locals)
        self.orig_completer = None
        self.orig_displayhook = None
        self.completed_keys = []
        self.history = {}
        self.history_num = 0
        self.loop = asyncio.get_event_loop()
        self.locals['h'] = self.history

    def interact(self, *args, **kwargs):

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