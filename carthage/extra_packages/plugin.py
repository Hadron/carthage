# Copyright (C) 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.config import ConfigSchema
from carthage.config.types import ConfigPath
from carthage.dependency_injection import *
from carthage.machine import customization_task


class ExtraPackagesConfig(ConfigSchema, prefix="extra_packages"):

    architectures: str = "amd64 source"
    components: str = "main contrib non-free proprietary"

    #: Accept any .changes file from this directory
    packages_dir: ConfigPath

    repository_dir: ConfigPath = "{state_dir}/extra_packages_repo"


@inject(injector=Injector)
def carthage_plugin(injector):
    from .repo import ExtraRepository, AddExtraRepo, UpgradeFromRepo
    from carthage.hadron.images import HadronImageMixin
    from carthage.hadron.build_database import RouterMixin, NonRouterMixin
    HadronImageMixin.add_extra_repo = customization_task(
        AddExtraRepo,
        before=HadronImageMixin.setup_hadron_packages)
    for c in (NonRouterMixin, RouterMixin):
        c.upgrade_from_extra_repo = customization_task(
            UpgradeFromRepo, before=RouterMixin.ansible_initial_router)
        c.add_extra_repo = customization_task(
            AddExtraRepo,
            before=c.upgrade_from_extra_repo)

    injector.add_provider(ExtraRepository)
