#!/usr/bin/python3
# Copyright (C) 2018, 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


from setuptools import setup

setup(
    name = "carthage",
    license = "LGPL-3",
    include_package_data = True,
    maintainer = "Sam Hartman",
    maintainer_email = "sam.hartman@hadronindustries.com",
    url = "https://github.com/hadron/carthage",
    packages = ["carthage",
                'carthage.config',
                'carthage.dependency_injection',
                'carthage.network',
                'carthage.extra_packages',
                "carthage.modeling",
                'carthage.vault',
                'carthage.podman',
                "carthage.vmware"],
    install_requires = ['pytest', ],
    scripts = ['bin/carthage-runner',
               'bin/carthage-console',
               'bin/carthage-vault-tool',
               'bin/btrfs-rmrf'],
    package_data = {'carthage': ['resources/templates/**',
                                 'resources/*',],
                    'carthage.extra_packages': ['resources/*'],
    },
    version = "0.20",
)

