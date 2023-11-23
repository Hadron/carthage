# Copyright (C) 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


from pathlib import Path
import pkg_resources
import typing
from .dependency_injection import inject, Injector, AsyncInjector
from .plugins import CarthagePlugin
from . import sh
__all__ = []

dependency_preference_order = ['deb', 'pypi']

@inject(injector=Injector)
def plugin_iterator(injector):
    for key, plugin in injector.filter_instantiate(
            CarthagePlugin, ['name']):
        yield plugin

__all__ += ['plugin_iterator']

def collect_dependencies(
        plugins: list[CarthagePlugin],
        descend_plugins: typing.Union[list,bool]=[],
        minimize_os_packages:bool = True,
        valid_dependency_types = dependency_preference_order,
) -> list[dict[str, str]]:
    '''
    Iterate over the plugins.  Return dependencies of the form ``{type:dependency}`` for each dependency in a plugin.  Dependencies may be expressed multiple times.

    :param descend_plugins: A list of  plugins where even if the plugin is installed as a python distribution, we should process pypi dependencies.  By default, if a plugin is installed as a distribution, we assume that some OS or PyPi installation has taken care of installing its Python requirements.  (We may need to do something similar for OS level requirements if plugins start being installed as OS packages).  This is a list of plugins where we return all their dependencies even if they are installed as python distributions.  The primary case where this is used is so that the list of dependencies for plugins bundled with Carthage core can be generated or so a plugin's pyproject.toml can be updated from its Carthage metadata.

    :param minimize_os_packages: If a dependency is available both as an OS package and a PyPI distribution, we typically prefer the OS package.  However, at least when installing packages, there is no need to install an OS package if the distribution is already available either via OS installation or PyPi.  If this parameter is true, skip dependencies for any dependency having a PyPi satisfaction that is already installed.  This only makes sense for installs, and not for generating dependency lists.

    '''
    results: list[dict] = []
    for plugin in plugins:
        if not 'dependencies' in plugin.metadata: continue
        skip_pypi_package = False # Set per plugin based on descend_plugins
        if 'package' in plugin.metadata:
            if not ( descend_plugins is True or plugin.name in descend_plugins):
                try:
                    pkg_resources.get_distribution(plugin.metadata['package'])
                    skip_pypi_package = True
                except pkg_resources.ResolutionError: pass

        for dependency in plugin.metadata['dependencies']:
            assert isinstance(dependency, dict)
            skip_os_package = minimize_os_packages and 'pypi' in dependency # set to False below; decided per dependency
            if minimize_os_packages and 'pypi' in dependency:
                for requirement in pkg_resources.parse_requirements(dependency['pypi']):
                    # We only expect one requirement but the interface returns an iterator
                    # This code would be better if it checked the installed version satisfied the requirement
                    try: pkg_resources.get_distribution(requirement.name)
                    except pkg_resources.ResolutionError:
                        skip_os_package = False
                        break
            for dependency_type in valid_dependency_types:
                # we return the first dependency that is valid,
                # unless we choose to skip that dependency.  If we
                # skip a dependency, no dependency is returned; we
                # skip when none is needed.
                if dependency_type in dependency: break
            else: # This else needs to belong to the for loop not the if
                dependency_type = None
            if dependency_type == 'deb':
                if skip_os_package: continue
                results.append(dict(deb=dependency['deb']))
            elif dependency_type == 'pypi':
                if skip_pypi_package: continue
                results.append(dict(pypi=dependency['pypi']))
    return results

__all__ += ['collect_dependencies']

@inject(injector=Injector)
async def install_deb(*, injector, **kwargs):
    plugins = list(injector(plugin_iterator))
    # Foreground and stem probably become configurable if we want to
    # be able to turn off sudo or install remotely
    foreground = True
    stem = sh
    try: stem = stem.sudo.bake()
    except sh.CommandNotFound: pass
    packages:list[str] = []
    for dependency in collect_dependencies(plugins, **kwargs):
        if 'deb' in dependency:
            packages.append(dependency['deb'])
            # if not a deb dependency then deb is not the most
            # preferred way to install; our caller could have adjusted
            # valid_dependency_types if they wanted deb more
            # preferred.
    if packages:
        stem.apt(
            '-y', '--no-install-recommends',
            'install', *packages, _fg=foreground)

@inject(injector=Injector)
async def install_pypi(*, sudo=True,
                       allow_system=True,
                       injector, **kwargs):
    stem = sh
    foreground = True
    if sudo:
        try: stem = getattr(stem, 'sudo').bake()
        except sh.CommandNotFound: pass
    requirements = []
    for dependency in collect_dependencies(plugins, **kwargs):
        if 'pypi' in dependency: requirements.append(dependency['pypi'])
    options = []
    if allow_system: options.append('--break-system-packages')
    if requirements:
        await stem.pip3(
            'install',
            *options,
            *requirements,
            _fg=foreground)

@inject(ainjector=AsyncInjector)
async def install_carthage_dependencies(
        minimize_os_packages:bool = False,
        valid_dependency_types:list = dependency_preference_order,
        *, ainjector):
    await ainjector(
        install_deb,
        minimize_os_packages=minimize_os_packages,
        valid_dependency_types=valid_dependency_types)
    
__all__ += ['install_carthage_dependencies']


@inject(injector=Injector)
def gen_requirements(descend_plugins=[],
                     *,
                     injector):
    plugins = list(injector(plugin_iterator))
    requirements = []
    for dependency in collect_dependencies(plugins,
                                           valid_dependency_types=['pypi'],
                                           minimize_os_packages=False,
                                           descend_plugins=descend_plugins):
        requirements.append(dependency['pypi'])
    return requirements

__all__ += ['gen_requirements']

@inject(injector=Injector)
def gen_os_dependencies(package_type: str = 'deb',
                        minimize_os_packages: bool=False,
                     *,
                     injector):
    plugins = list(injector(plugin_iterator))
    packages = []
    for dependency in collect_dependencies(
            plugins,
            valid_dependency_types=[package_type],
            minimize_os_packages=minimize_os_packages,
                                           ):
        packages.append(dependency[package_type].strip())
    return packages

__all__ += ['gen_os_dependencies']

@inject(injector=Injector)
def gen_requirements_command(args, injector):
    print("\n".join(injector(gen_requirements, args.descend_plugins)))

__all__ += ['gen_requirements_command']

@inject(ainjector=AsyncInjector)
async def install_carthage_dependencies_command(args, ainjector):
    valid_dependency_types = dependency_preference_order
    if args.prefer_python:
        valid_dependency_types = ['pypi', 'deb']
    await ainjector(
        install_carthage_dependencies,
        minimize_os_packages=args.minimize_os,
        valid_dependency_types=valid_dependency_types)

__all__ += ['install_carthage_dependencies_command']

def setup_deployment_commands(command_action):
    gen_requirements = command_action.add_parser(
        'generate_requirements',
        help='Generate a set of Python requirements either for a requirements.txt or for use in pyproject.toml')
    gen_requirements.add_argument(
        '--descend-plugins',
        nargs='+',
        default=[],
        help='Plugins whose internal dependencies should be included'
        )
    install_dependencies = command_action.add_parser(
        'install_dependencies', 
        help='Install Carthage dependencies')
    install_dependencies.add_argument(
        '--no-minimize-os',
        dest='minimize_os',
        action='store_false',
        default=True,
        help='Do not minimize OS package installs when a Python distribution is already installed')
    install_dependencies.add_argument(
        '--prefer-python',
        action='store_true',
        help='Prefer pypi to OS packages')
    
    
__all__ += ['setup_deployment_commands']
