# Copyright (C) 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import os
from glob import glob
from carthage.setup_tasks import setup_task, SetupTaskMixin, cross_object_dependency, SkipSetupTask
from carthage.dependency_injection import *
from carthage import ConfigLayout
from carthage.machine import FilesystemCustomization
from carthage.utils import memoproperty

from carthage import sh


@inject(config=ConfigLayout)
class ExtraRepository(SetupTaskMixin, AsyncInjectable):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.stamp_path = self.config.extra_packages.repository_dir

    @memoproperty
    def path(self):
        return self.config.extra_packages.repository_dir

    @setup_task("Create Repository")
    def create_repository(self):
        repo_dir = self.config.extra_packages.repository_dir
        conf_dir = os.path.join(repo_dir, 'conf')
        config = self.config
        os.makedirs(conf_dir, exist_ok=True)
        with open(os.path.join(conf_dir, 'distributions'), 'wt') as f:
            f.write(f'''\
Codename: carthage_extra
Suite: carthage_extra
Architectures: {config.extra_packages.architectures}
Components: {config.extra_packages.components}

'''
                    )

    @setup_task("Add Packages to Repository")
    async def add_packages(self):
        packages_dir = self.config.extra_packages.packages_dir
        repo_dir = self.config.extra_packages.repository_dir
        for c in glob(os.path.join(packages_dir, '*.changes')):
            await sh.reprepro(
                "--ignore=wrongdistribution",
                "include", "carthage_extra",
                c,
                _bg=True, _bg_exc=True,
                _cwd=repo_dir)

    @add_packages.invalidator()
    def add_packages(self, **kwargs):
        last_run = self.check_stamp(self.__class__.add_packages.stamp)[0]
        if not last_run:
            return False
        packages_dir = self.config.extra_packages.packages_dir
        for c in glob(os.path.join(packages_dir, "*.changes")):
            st = os.stat(c)
            if st.st_mtime > last_run:
                return False
        return True  # Not invalidated

    def source_lines(self):
        repository_dir = self.config.extra_packages.repository_dir
        if os.path.exists(os.path.join(
                repository_dir, "dists")):
            return(f'''
deb [trusted=yes] file:///extra_packages carthage_extra {self.config.extra_packages.components}
''')
        return ""


@inject(repo=ExtraRepository)
class AddExtraRepo(FilesystemCustomization):

    description = "Add extra repository to machine"

    repo_dependency = cross_object_dependency(ExtraRepository.add_packages, "repo")

    @setup_task("Create Extra Repository Sources")
    def manage_sources(self):
        if not self.repo.source_lines():
            try:
                os.unlink(os.path.join(self.path,
                                       "etc/apt/sources.list.d/extra_repo.list"))
            except FileNotFoundError:
                pass
            raise SkipSetupTask()
        with open(os.path.join(self.path, "etc/apt/sources.list.d/extra_repo.list"), "wt") as f:
            f.write(self.repo.source_lines())
        with open(os.path.join(self.path, "etc/apt/preferences.d/10-extra.pref"), "wt") as f:
            f.write('''\
package: *
pin: release n=carthage_extra
pin-priority: 1030
''')

    @setup_task("Copy in extra repo")
    async def copy_in_repo(self):
        j = os.path.join
        if not self.repo.source_lines():
            raise SkipSetupTask()
        await sh.rsync(
            '-a',
            '--delete-delay',
            j(self.repo.path, "dists"),
            j(self.repo.path, "pool"),
            j(self.path, "extra_packages"),
            _bg=True,
            _bg_exc=False)


@inject(repo=ExtraRepository)
class UpgradeFromRepo(FilesystemCustomization):

    description = "Schedule apt update/apt full-upgrade"

    repo_dependency = cross_object_dependency(ExtraRepository.add_packages, 'repo')

    @setup_task('update packages')
    async def update_packages(self):
        await self.run_command('apt', 'update',
                               _bg=True, _bg_exc=False)

    @setup_task("upgrade packages")
    async def upgrade_packages(self):
        await self.run_command(
            'sh', '-c',
            'DEBIAN_FRONTEND=NONINTERACTIVE apt --allow-downgrades -y full-upgrade',
            _bg=True,
            _bg_exc=False)
