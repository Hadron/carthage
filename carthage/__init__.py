# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import carthage. dependency_injection
import carthage.config

base_injector = carthage.dependency_injection.Injector()
base_injector.add_provider(carthage.config.ConfigLayout)
base_injector.add_provider(asyncio.get_event_loop())
