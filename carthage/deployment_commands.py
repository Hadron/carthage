# Copyright (C) 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import argparse
import carthage.deployment
from .dependency_injection import inject, Injector
from .deployment import *
from .console import CarthageRunnerCommand

__all__ = []

class DeploymentCommand(CarthageRunnerCommand):

    force_readonly:bool = False # Typically destroy finds  deployables readonly
    method: str #: Which function in carthage.deployment to run for
                #the actual deploy
    def setup_subparser(self, subparser):
        '''Set up arguments common to all of the deployment commands'''
        subparser.add_argument('--dry-run', '-n',
                               action='store_true',
                               default=False,
                               help='Only perform a dry run')
        subparser.add_argument('--force-confirm', '-y',
                               action='store_true',
                               help='Skip printing a dry run report and immediately perform the deployment')
        subparser.add_argument('--report-out', '-o',
                               type=argparse.FileType('wt'),
                               help='Where to write output report for the final deployment report')
        
        

    async def run(self, args):
        '''Execute deployment with optional dry run step
        '''
        if args.force_confirm and args.dry_run:
            raise RuntimeError('Cannot force confirm and run in dry run only mode')
        ainjector = self.ainjector
        deployables = await ainjector(find_deployables, readonly=self.force_readonly or args.dry_run,
                                      recurse=(self.method == 'run_deployment_destroy'))
        method_func = getattr(carthage.deployment, self.method)
        if not args.force_confirm:
            dry_run_results = await ainjector(method_func, dry_run=True, deployables=deployables)
            print(dry_run_results.report(dry_run=True))
            if not args.dry_run:
                # If we are just doing a dry run, that's all
                # Otherwise we need to confirm
                confirmation = input("Run this deployment(y/n)?")
                if confirmation != 'y':
                    print("Deployment aborted.")
                    raise SystemExit(2)
        # Again, on dry run only, we do not run the actual deployment
        # By this point either the deployment has been confirmed by
        # the user or by args.force_confirmation
        if not args.dry_run:
            result = await ainjector(method_func, deployables=deployables)
            print(result.report(), file=args.report_out, flush=True)
            if args.report_out:
                # summary to stdout if main report to file
                print(result.summary)

class DeployCommand(DeploymentCommand):

    name = 'deploy'
    method = 'run_deployment'

    subparser_kwargs = {
        'help': 'Deploy all deployables in the layout',
        }
    
class DestroyCommand(DeploymentCommand):

    name = 'destroy'
    method = 'run_deployment_destroy'
    subparser_kwargs = {
        'help': 'Destroy all Deployables in the layout where the destroy_policy does not retain the object.',
        }
    

@inject(injector=Injector)
def register(injector):
    injector.add_provider(DeployCommand, allow_multiple=True)
    injector.add_provider(DestroyCommand, allow_multiple=True)
            
