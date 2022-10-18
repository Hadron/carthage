# Copyright (C) 2022, Hadron Industries, Inc.
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

class MachineCommand(CarthageRunnerCommand):

    async def should_register(self):
        from .machine import Machine
        return  any(self.ainjector.filter(Machine, ['host']))

    def setup_subparser(self, subparser):
        subparser.add_argument('machine')

        
class StartCommand(MachineCommand):

    name = 'start'

    async def run(self,args):
        machine = await self.ainjector.get_instance_async(InjectionKey(Machine, host=args.machine))
        if args.ready: await machine.async_become_ready()
        await machine.start_machine()

    def setup_subparser(self, parser):
        parser.add_argument('--ready',
                            action=argparse.BooleanOptionalAction,
                            help='Bring machine to async_ready before starting')
        super().setup_subparser(parser)

class StopCommand(MachineCommand):

    name = 'stop'

    async def run(self,args):
        machine = await self.ainjector.get_instance_async(InjectionKey(Machine, host=args.machine))
        await machine.stop_machine()

class ListMachines(MachineCommand):

    name ='list-machines'

    def setup_subparser(self, parser): pass

    async def run(self, args):
        machines = self.ainjector.filter(Machine, ['host'])
        for m in machines:
            print(m.host)
            
def enable_runner_commands(ainjector):
    ainjector.add_provider(StartCommand)
    ainjector.add_provider(ListMachines)
    
    ainjector.add_provider(StopCommand)
    
