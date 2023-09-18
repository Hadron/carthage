# Copyright (C) 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from .base import MachineModel, InjectableModel
from carthage import Machine, when_needed, ConfigLayout
from ..ansible import AnsibleGroupPlugin, AnsibleHostPlugin, AnsibleInventory
from ..dependency_injection import *


class ModelingGroupPlugin(AnsibleGroupPlugin):

    name = 'modeling'

    async def groups_for(self, m):
        if hasattr(m, 'model'):
            return getattr(m.model, 'ansible_groups', [])
        return []

    async def group_info(self):
        return {}


class ModelingHostPlugin(AnsibleHostPlugin):

    name = 'modeling'

    async def host_vars(self, m: Machine):
        try:
            model = m.model
        except BaseException:
            return {}
        return getattr(model, 'ansible_vars', {})


def enable_modeling_ansible(injector: Injector):
    injector.add_provider(InjectionKey(AnsibleGroupPlugin, name='modeling'), ModelingGroupPlugin)
    injector.add_provider(InjectionKey(AnsibleHostPlugin, name='modeling'), ModelingHostPlugin)



class AnsibleModelMixin(InjectableModel, AsyncInjectable):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.config_layout = self.injector(ConfigLayout)
        self.injector.add_provider(
            InjectionKey(AnsibleInventory),
            when_needed(AnsibleInventory,
                        destination=self.config_layout.output_dir + "/inventory.yml"))
        enable_modeling_ansible(self.injector)

    async def generate(self):
        await self.ainjector.get_instance_async(AnsibleInventory)


__all__ = ['enable_modeling_ansible', 'AnsibleModelMixin']
