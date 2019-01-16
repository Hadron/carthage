# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import carthage. dependency_injection, carthage.config
import carthage.config
from .dependency_injection import AsyncInjector, Injector, InjectionKey, inject, Injectable, AsyncInjectable
from .config import ConfigLayout, config_key

import carthage.hadron_layout
import carthage.hadron
import carthage.container
import carthage.ssh
import carthage.pki
import carthage.vm

base_injector = carthage.dependency_injection.Injector()
carthage.config.inject_config(base_injector)
base_injector.add_provider(ssh.SshKey)
base_injector.add_provider(ssh.AuthorizedKeysFile)
base_injector.add_provider(asyncio.get_event_loop(), close = False)
base_injector.add_provider(carthage.hadron_layout.fake_internet)
base_injector.add_provider(carthage.hadron_layout.external_network)
base_injector.add_provider(carthage.hadron_layout.database_key, carthage.hadron_layout.test_database_container)
base_injector.add_provider(carthage.container.ssh_origin, carthage.hadron_layout.test_database_container)

base_injector.add_provider(carthage.container.container_image, carthage.hadron.hadron_container_image)
base_injector.add_provider(carthage.vm.vm_image, carthage.hadron.hadron_vm_image)
base_injector.add_provider(InjectionKey(carthage.ssh.SshAgent), carthage.ssh.ssh_agent)
base_injector.add_provider(carthage.pki.PkiManager)
