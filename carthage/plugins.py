# Copyright (C) 2021, 2022, 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import dataclasses
import importlib
import logging
import re
import sys
import types
import typing
import yaml
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec, find_spec
from typing import Union
from urllib.parse import urlparse
from .dependency_injection import *
from .config import ConfigLayout
from .files import checkout_git_repo


logger = logging.getLogger('carthage.plugins')

@dataclasses.dataclass(frozen=True)
class PluginMapping:
    map:str
    to: str
    final:bool = False
    regexp:bool = False

    def map_url(self, url):
        '''
        Returns url, matched
        '''
        if bool(self.regexp) is False:
            if self.map not in url:
                return url, False
            return url.replace(self.map, self.to), True
        else: # regexp
            if re.search(self.map, url):
                return re.sub(self.map, self.to, url), True
            return url, False

class PluginMappings(Injectable):

    '''
    A collection of plugin mappins.  Often it is desirable to rewrite the URL for proprietry plugins, or to choose between https and ssh access to a git server.
    In the configuration file, a mapping takes the form of a list of dictionaries having the following form:

    map
        The value to map from.

    to
        The value to replace *map* with.

    final
        Defaults to false. If true, then if the *map* value matches, this will be the last mapping executed for the given spec.

    regexp
        Defaults to false. If true, *map* is interpreted as a regular expression and *to* as a substitution pattern.

    '''

    mappings: list[PluginMapping]

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mappings = []
        
    def add_mapping(self, mapping:dict):
        '''
        Adds a mapping to the collection. Mappings should be added in preferred order--the first mapping added will be highest priority.
        That means that as mappings are read in from configuration, the first configuration source processed will be highest priority.
        '''
        for k in ('map', 'to'):
            if k not in mapping:
                raise TypeError(f'{k} is required for a plugin mapping')
        if set(mapping.keys()) - {'map', 'to', 'final', 'regexp'}:
            raise TypeError(f'Unexpected keys in mapping {mapping}')
        self.mappings.append(PluginMapping(**mapping))

    def map(self, spec):
        '''
        Map the URL in *spec*.
        If after mapping there is no ``:`` in the URL, convert to a path spec.
        '''
        if 'url' not in  spec:
            return spec
        url = spec['url']
        for mapping in self.mappings:
            url, matched = mapping.map_url(url)
            if matched and mapping.final: break
        if url != spec['url']:
            result = _parse_plugin_spec(url)
            result.update({k:spec[k] for k in spec.keys() if (k not in result) and k != 'url'})
            return result
        return spec
    
class CarthagePlugin(Injectable):

    name: str
    package: typing.Optional[importlib.resources.Package]
    resource_dir: Path
    metadata: dict
    import_error: str = None

    def __init__(self, name: str, package: importlib.resources.Package,
                 metadata: dict,
                 **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.package = package
        if 'resource_dir' in metadata:
            self.resource_dir = Path(metadata['resource_dir'])
        else:
            self.resource_dir = Path(package.__path__[0])
        self._resources = {}
        self.metadata = metadata

    def _get_resource(self, resource):
        p = self.resource_dir.joinpath(resource)
        try:
            return self._resources[p]
        except BaseException:
            if p.exists():
                self._resources[p] = p
                return p
            else:
                self._resources[p] = None
                return None

    def contains_resource(self, resource):
        return bool(self._get_resource(resource))

    # Yes this fails for zips and similar.
    # If we care, we'd need to go to a lot of trouble to unpack things,
    # because we do need to make directories available for things like ansible
    # plays.
    def resource_path(self, resource):
        return self._get_resource(resource)
plugin_spec = Union[str, dict, Path]

def _parse_plugin_spec(spec: plugin_spec):
    if isinstance(spec, dict):
        return spec
    if hasattr(spec, '__fspath__'):
        return dict(type='path', path=spec.resolve())
    assert isinstance(spec, str)
    if ':' in spec:
        prefix = spec.partition(':')[0]
        if prefix in ('https', 'git+ssh'):
            return dict(type='git', url=spec)
        raise NotImplementedError(f'unrecognized plugin specification: {spec}')
    if '/' in spec or spec == '.' or spec == '..':
        return dict(type='path', path=Path(spec).resolve())
    return dict(type='module', name=spec)

@inject(injector=Injector)
def load_plugin(spec: plugin_spec,
                *, injector,
                ignore_import_errors=False):

    '''
    Load a plugin from a plugin specification:

    * A path name to a directory containing a ``carthage_plugin.yml``

    * A python package name

    * A ``https`` URL to a Git repository

    * A ``git+ssh`` URL to a git repository

    :param ignore_import_errors:  If True, succeed and register the plugin even if the python code raises.  This is intended to allow the plugin to be loaded so its metadata can be examined to determine dependencies.  Obviously the plugin is unlikely to be functional in such a state.
    '''
    spec = _parse_plugin_spec(spec)
    orig_spec = spec
    plugin_mappings = injector.get_instance(PluginMappings)
    spec = plugin_mappings.map(spec)
    if spec != orig_spec:
        logger.debug('Mapped %s to %s', orig_spec, spec)
    

    if spec['type'] == 'module':
        module_name = spec['name']
        module_spec = find_spec(module_name)
        if not module_spec:
            raise ValueError(f"no module found with name '{module_name}'")
        return handle_module_spec(module_spec=module_spec, injector=injector, ignore_import_errors=ignore_import_errors, metadata=None)

    elif spec['type'] == 'path':
        return handle_path_url(spec['path'], injector, ignore_import_errors=ignore_import_errors)

    elif spec['type'] == 'git':
        path = handle_git_url(spec, injector)
        return handle_path_url(path, injector, ignore_import_errors=ignore_import_errors)

    else:
        raise ValueError(f'unrecognized plugin type in {spec}')

def handle_path_url(spec: dict, injector, ignore_import_errors):
    path = Path(spec).resolve()
    metadata_path = path / "carthage_plugin.yml"
    if not metadata_path.exists():
        raise FileNotFoundError(f'{metadata_path} not found')
    metadata = yaml.safe_load(metadata_path.read_text())
    if 'resource_dir' not in metadata:
        metadata['resource_dir'] = path
    if 'name' not in metadata:
        raise ValueError(f'metadata must contain a name when loading plugin from path')
    # Stop early if already loaded
    try:
        injector.get_instance(InjectionKey(CarthagePlugin, name=metadata['name']))
        logger.debug(f'Plugin {metadata["name"]} already loaded')
        return
    except KeyError:
        pass
    _handle_plugin_config(injector, metadata, metadata_path, ignore_import_errors=ignore_import_errors)
    try:
        python_path = metadata['python']
        python_path = str(path.joinpath(python_path))
        if python_path not in sys.path:
            sys.path.insert(0, python_path)
    except KeyError:
        pass
    if 'package' in metadata:
        module_spec = find_spec(metadata['package'])
    else:
        package_path = path.joinpath("carthage_plugin.py")
        name = metadata['name']
        if '.' not in name:
            name = "carthage.carthage_plugins." + name
            _setup_carthage_plugins_module()
        if package_path.exists():
            module_spec = spec_from_file_location(
                name, location=package_path,
                submodule_search_locations=[str(path / "python")]
            )
        else:
            module_spec = None
    return handle_module_spec(injector, module_spec=module_spec, metadata=metadata,
                              ignore_import_errors=ignore_import_errors)

def handle_module_spec(injector, *, module_spec, metadata, ignore_import_errors):
    package = None
    import_error = None
    if module_spec:
        # For some reason module_from_spec sometimes changes spec.name
        module_name = module_spec.name
        if module_name in sys.modules:
            package = sys.modules[module_name]
        else:
            package = module_from_spec(module_spec)
            try:
                parent_module = None
                parent, _, stem = module_name.rpartition('.')
                if parent:
                    parent_module = importlib.import_module(parent)
                    setattr(parent_module, stem, package)

                sys.modules[module_name] = package
                module_spec.loader.exec_module(package)
            except BaseException as e:
                try: del sys.modules[module_name]
                except KeyError: pass
                if parent_module: delattr(parent_module, stem)
                if ignore_import_errors:
                    logger.debug('Ignoring error importing %s: %s', module_spec.name, str(e))
                    import_error = str(e)
                else:
                    raise
    return injector(load_plugin_from_package, package, metadata,
                    ignore_import_errors=ignore_import_errors,
                    import_error=import_error)

def handle_git_url(spec, injector):
    parsed = urlparse(spec['url'])
    config = injector(ConfigLayout)
    stem = Path(parsed.path).name
    if stem.endswith('.git'):
        stem = stem[:-4]
    dest = Path(config.checkout_dir) / stem
    if dest.exists():
        return dest
    logger.info(f'Checking out {parsed.geturl()}')
    branch = spec.get('branch', None)
    injector(checkout_git_repo, parsed.geturl(), dest, branch=branch, foreground=True)
    return dest


@inject(injector=Injector)
def load_plugin_from_package(package: typing.Optional[types.ModuleTyp],
                             metadata: dict = None,
                             *, ignore_import_errors=False,
                             import_error=None,
                             injector):
    if (not metadata) and (not package):
        raise RuntimeError('Either package or metadata must be supplied')
    if metadata:
        if 'resource_dir' in metadata:
            metadata_path = Path(metadata['resource_dir']) / "carthage_plugin.yml"
        else:
            metadata_path = Path(package.__file__)
    if not metadata:
        if not package.__spec__.origin:
            raise SyntaxError(f'{package.__name__} is not a Carthage plugin')
        try:
            metadata = yaml.safe_load(importlib.resources.files(
                package).joinpath('carthage_plugin.yml').read_text())
            metadata_path = package.__file__
        except (FileNotFoundError, ImportError):
            # consider the case of hadron-operations
            # plugin is hadron.carthage
            # but when not installed resources live at the top level of the checkout.
            components = len(package.__name__.split("."))
            path_root = Path(package.__file__).parents[components]
            if path_root.joinpath("carthage_plugin.yml").exists():
                metadata = yaml.safe_load(path_root.joinpath("carthage_plugin.yml").read_text())
                metadata_path = path_root.joinpath('carthage_plugin.yml')
                if 'resource_dir' not in metadata:
                    metadata['resource_dir'] = path_root
            else:
                metadata = {}
                metadata_path = None

    if 'name' in metadata:
            name = metadata['name']
    else:
        name = package.__name__
    try:
        injector.get_instance(InjectionKey(CarthagePlugin, name=name))
        return # already loaded
    except KeyError: pass
    _handle_plugin_config(injector=injector, metadata=metadata, path=metadata_path, ignore_import_errors=ignore_import_errors)
    try:
        plugin_module = importlib.import_module(".carthage_plugin", package=package.__name__)
    except (ImportError, AttributeError):
        plugin_module = package
        # note plugin_module may be none if package is none
    plugin_func = getattr(plugin_module, "carthage_plugin", None)
    if not any((plugin_func, metadata)):
        raise SyntaxError(f'{package.__file__} is not a Carthage plugin')

    if package and 'package' not in metadata:
        metadata['package'] = package.__name__
    if plugin_func:
        try:
            res = injector(plugin_func)
        except Exception as e:
            res = None
            if not ignore_import_errors: raise
            if not import_error: import_error = e
    else:
        res = None
    if isinstance(res, CarthagePlugin):
        assert res.name == name, "Metadata name must agree with resulting plugin for duplicate load detection to work"
        plugin_object = res
    else:
        plugin_object = injector(CarthagePlugin, name=name, package=package, metadata=metadata)
    if import_error: plugin_object.import_error = import_error
    injector.add_provider(
        InjectionKey(CarthagePlugin, name=plugin_object.name),
        plugin_object)


def _handle_plugin_config(injector, metadata, path, ignore_import_errors):
    # we don't want to take a ConfigLayout as a dependency because
    # that tends to push its instantiation too high in the injector
    # hierarchy
    config = injector(ConfigLayout)
    if 'config' in metadata:
        config.load_yaml(yaml.dump(metadata['config']), path=path, ignore_import_errors=ignore_import_errors)


def _setup_carthage_plugins_module():
    from types import ModuleType
    sys.modules['carthage.carthage_plugins'] = ModuleType('carthage.carthage_plugins')
__all__ = ['load_plugin', 'load_plugin_from_spec']
