# Copyright (C) 2022, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import argparse
from .console import CarthageRunnerCommand
from . import *
from . import kvstore
import asyncio

class MachineCommand(CarthageRunnerCommand):

    async def should_register(self):
        from .machine import Machine
        return any(self.ainjector.filter(Machine, ['host']))

    def setup_subparser(self, subparser):
        subparser.add_argument('machine')


class StartCommand(MachineCommand):

    name = 'start'

    async def run(self, args):
        machine = await self.ainjector.get_instance_async(InjectionKey(Machine, host=args.machine))
        if args.ready:
            if isinstance(machine.model, AsyncInjectable):
                await machine.model.async_become_ready()
            await machine.async_become_ready()
        await machine.start_machine()

    def setup_subparser(self, parser):
        parser.add_argument('--ready',
                            action=argparse.BooleanOptionalAction,
                            help='Bring machine to async_ready before starting')
        super().setup_subparser(parser)


class StopCommand(MachineCommand):

    name = 'stop'

    async def run(self, args):
        machine = await self.ainjector.get_instance_async(InjectionKey(Machine, host=args.machine))
        await machine.stop_machine()

class DeleteCommand(MachineCommand):

    name = 'delete'

    async def run(self, args):
        machine = await self.ainjector.get_instance_async(InjectionKey(Machine, host=args.machine))
        if hasattr(machine, 'delete'):
            await machine.delete()
        else:
            raise NotImplementedError(f'{machine} cannot be deleted')

class ListMachines(MachineCommand):

    name = 'list-machines'

    def setup_subparser(self, parser): pass

    async def run(self, args):
        machines = self.ainjector.filter(Machine, ['host'])
        for m in machines:
            print(m.host)

class SleepCommand(CarthageRunnerCommand):

    name = 'sleep'

    subparser_kwargs = dict(help='Disable the console command loop to prioritize PDB.')

    def setup_subparser(self, parser):
        pass

    async def run(self, args):
        # If no Pdb is active, this will return ~immediately.  If a
        # Pdb is active, this will yield the console until the Pdb is
        # continued or closed.
        await asyncio.sleep(0.01)

class DumpAssignmentsCommand(CarthageRunnerCommand):

    name = 'dump_assignments'

    subparser_kwargs = dict(
        help='Dump out assignments such as MAC addresses and IP addresses related to this layout.',
        )
    
    async def should_register(self):
        try:
            self.persistent_seed_path = await self.ainjector.get_instance_async(persistent_seed_path)
            return True
        except KeyError: return False

    def setup_subparser(self, parser):
        parser.add_argument('path',
                            default=self.persistent_seed_path,
                            nargs='?',
                            help=f'Path where assignments are dumped; defaults to {self.persistent_seed_path}')
        

    async def run(self, args):
        from carthage.modeling import CarthageLayout
        store = await self.ainjector.get_instance_async(kvstore.KvStore)
        layout = await self.ainjector.get_instance_async(CarthageLayout)
        models = await layout.all_models(ready=False)
        self.model_names = set((getattr(m, 'name',"") for m in models))
        store.dump(args.path, self.dump_filter)

    def dump_filter(self, domain, key, value):
        # Several domains have keys of the form model|interface
        if domain == 'mac' or domain.endswith('/hints') :
            name, sep, interface = key.partition('|')
            if sep:
                if name in self.model_names: return True
                return False
        # We do not recognize the structure so permit
        return True
    

def enable_runner_commands(ainjector):
    ainjector.add_provider(StartCommand)
    ainjector.add_provider(ListMachines)
    ainjector.add_provider(SleepCommand)
    ainjector.add_provider(StopCommand)
    ainjector.add_provider(DeleteCommand)
    ainjector.add_provider(DumpAssignmentsCommand)

