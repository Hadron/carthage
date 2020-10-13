#!/usr/bin/python3

from setuptools import setup

setup(
    name = "carthage",
    license = "proprietary",
    maintainer = "Sam Hartman",
    maintainer_email = "sam.hartman@hadronindustries.com",
    url = "http://www.hadronindustries.com/",
    packages = ["carthage", "carthage.hadron",
                'carthage.config',
                'carthage.extra_packages',
                "carthage.vmware"],
    install_requires = ['pytest', ],
    scripts = ['bin/carthage-runner',
               'bin/carthage-console',
               'bin/carthage-vault-tool',
               'bin/btrfs-rmrf'],
    package_data = {'carthage': ['resources/templates/*'],
                    'carthage.extra_packages': ['resources/*'],
    },
    version = "0.6",
)

