# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import importlib, types, typing, sys
import yaml
from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec
from .dependency_injection import *


class CarthagePlugin(Injectable):

    name: str
    package: typing.Optional[importlib.resources.Package]
    resource_dir: Path

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

    def _get_resource(self, resource):
        p = self.resource_dir.joinpath(resource)
        try:
            return self._resources[p]
        except:
            if p.exists():
                self._resources[p] = p
                return p
            else:
                self._resources[p] = None
                return None

    def contains_resource(self, resource):
        return bool(self._get_resource(resource))

    # Yes this fails for zips and similar.
    #If we care, we'd need to go to a lot of trouble to unpack things, because we do need to make directories available for things like ansible plays.
    def resource_path(self, resource):
        return self._get_resource(resource)

@inject(injector = Injector)
def load_plugin(spec: str,
                *, injector):
    if hasattr(spec, "__fspath__") or '/' in spec:
        path = Path(spec)
        metadata_path = path/"carthage_plugin.yml"
        if not metadata_path.exists():
            raise FileNotFoundError(f'{metadata_path} not found')
        metadata = yaml.safe_load(metadata_path.read_text())
        if 'resource_dir' not in metadata:
            metadata['resource_dir'] = path
        if 'name' not in metadata:
            raise ValueError(f'metadata must contain a name when loading plugin from path')
        try:
            python_path = metadata['python']
            python_path = str(path.joinpath(python_path))
            if python_path not in sys.path: sys.path.append(python_path)
        except KeyError: pass
        if 'package' in metadata:
            package = importlib.import_module(metadata['package'])
        else:
            package_path = path.joinpath("carthage_plugin.py")
            name = metadata['name']
            if '.' not in name:
                name = "carthage.carthage_plugins."+name
            if package_path.exists():
                module_spec = spec_from_file_location(
                    name, location = package_path,
                    submodule_search_locations = [str(path/"python")]
                )
            else: module_spec = None
            if module_spec:
                package = module_from_spec(module_spec)
                sys.modules[name] = package
                module_spec.loader.exec_module(package)
            else:
                package = None
    else: #spec is a package
        package = importlib.import_module(spec)
        metadata = None
    return injector(load_plugin_from_package, package, metadata)


@inject(injector = Injector)
def load_plugin_from_package(package: typing.Optional[types.ModuleTyp],
                             metadata: dict = None,
                             *, injector):
    if (not metadata) and (not package):
        raise RuntimeError('Either package or metadata must be supplied')
    if not metadata:
        try:
            metadata = yaml.safe_load(importlib.resources.read_text(
                package, "carthage_plugin.yml"))
        except (FileNotFoundError, ImportError):
            # consider the case of hadron-operations
            # plugin is hadron.carthage
            # but when not installed resources live at the top level of the checkout.
            components = len(package.__name__.split("."))
            path_root = Path(package.__file__).parents[components]
            if path_root.joinpath("carthage_plugin.yml").exists():
                metadata = yaml.safe_load(path_root.joinpath("carthage_plugin.yml").read_text())
                if 'resource_dir' not in metadata: metadata['resource_dir'] = root_dir
            else:
                metadata = {}
                
                             
    try:
        plugin_module = importlib.import_module(".carthage_plugin", package = package.__name__)
    except (ImportError, AttributeError):
        plugin_module = package
        # note plugin_module may be none if package is none
    plugin_func = getattr(plugin_module, "carthage_plugin", None)
    if not any((plugin_func, metadata)):
        raise SyntaxError(f'{package.__file__} is not a Carthage plugin')
    
    if plugin_func:
        res = injector(plugin_func)
    else: res = None
    if isinstance(res, CarthagePlugin):
        plugin_object = res
    else:
        if 'name' in metadata:
            name = metadata['name']
        else:
            name = package.__name__
        plugin_object = injector(CarthagePlugin, name = name, package = package, metadata = metadata)
    injector.add_provider(
        InjectionKey(CarthagePlugin, name = plugin_object.name),
        plugin_object)

    
