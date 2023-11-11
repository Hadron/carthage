#!/usr/bin/python3
# Copyright (C) 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


import argparse
import os
import yaml
import mako.template

import carthage.vault
from carthage.dependency_injection import *
from carthage import base_injector, ConfigLayout
import carthage.utils

def main():
    parser = carthage.utils.carthage_main_argparser()
    parser.add_argument('--ca',
                        help = "Path of CA bundle")
    parser.add_argument(
        '--output-directory',
        '--output-dir',
        help ="Output directory for vault initialization")
    parser.add_argument( '--shares', default=1,
                         type=int, help = "Number of secret shares")
    parser.add_argument( '--threshold', default = 1,
                         type = int,
                         help = "Number of shares to unseal vault")
    parser.add_argument('--vault-config',
                        help = "YAML configuration specifying policies and authentication methods",
                        type =argparse.FileType("rt"))
    args = carthage.utils.carthage_main_setup(parser)
    carthage.utils.carthage_main_run(run, args)

@inject(config = ConfigLayout,
        ainjector = AsyncInjector)
async def run(args, config, ainjector):
    if args.ca:
        config.vault.ca_bundle = args.ca
    else:
        ca = os.getenv("VAULT_CACERT")
        if ca is not None: config.vault.ca_bundle = ca
        
    vault = await ainjector(carthage.vault.Vault)
    if not vault.client.sys.is_initialized():
        if not args.output_directory:
            raise ValueError("To initialize a vault you must specify an output directory")
        vault.initialize(args.output_directory, secret_threshold = args.threshold,
                         secret_shares = args.shares)
    if args.vault_config:
        vault.apply_config(
            yaml.safe_load(
                mako.template.Template(args.vault_config.read()).render()))


                         

        

if __name__ == '__main__' :
    main()
