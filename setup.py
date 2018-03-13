#!/usr/bin/python3

from setuptools import setup

setup(
    name = "carthage",
    license = "proprietary",
    maintainer = "Sam Hartman",
    maintainer_email = "sam.hartman@hadronindustries.com",
    url = "http://www.hadronindustries.com/",
    packages = ["carthage", "carthage.hadron"],
    install_requires = ['pytest', ],
    scripts = ['bin/carthage-runner'],
    package_data = {'carthage': ['resources/templates/*'],
    },
    version = "0.1",
)

