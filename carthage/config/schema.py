# Copyright (C) 2019, 2020, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import sys
from ipaddress import IPv6Address, IPv4Address, IPv4Network, IPv6Network
from pathlib import Path
import types
from ..dependency_injection import inject, Injectable, InjectionKey, Injector, partial_with_dependencies, InjectorClosed, InjectionFailed, injection_failed_unlogged


def config_key(k):
    return InjectionKey("config/" + k)


class ConfigResolutionFailed(ValueError):

    def __init__(self, k, val):
        self.config_key = k
        self.config_val = val
        super().__init__(f'Resolution of {k} with value `{val}` failed')


class ConfigSchemaMeta(type):

    def __new__(mcls, name, bases, namespace, *, prefix, **kwargs):
        cls = type.__new__(mcls, name, bases, namespace, **kwargs)
        if prefix != "" and not prefix.endswith('.'):
            prefix += '.'
        cls._schema = mcls._find_schema(prefix)
        try:
            annotations = cls._eval_annotations(cls.__annotations__)
        except AttributeError:
            annotations = {}
            # We do not yet parse docs
        schema = cls._schema
        for k in annotations:
            if k.startswith('_'):
                continue
            default = getattr(cls, k, None)
            if isinstance(default, dict):
                raise TypeError(
                    "A dict as a default for a configuration value is probably too confusing; discuss whether this is really what you want.")
            if k in schema:
                raise TypeError(f'{prefix}{k} is already defined')
            schema[k] = cls.Item(prefix + k, type_=annotations[k],
                                 default=default)
        return cls

    def subsections(cls, prefix):
        subsections = set()
        # prefix ends in a trailing period
        for k in cls._schemas:
            if k is prefix:
                continue
            if k.startswith(prefix):
                next = k[len(prefix):]
                next, sep, tail = next.partition('.')
                if next not in subsections:
                    subsections.add(next)
                    yield next

    @classmethod
    def _find_schema(cls, prefix):
        schemas = cls._schemas  # start at the root
        prefix = prefix.rstrip('.')
        try:
            return schemas[prefix]
        except KeyError:
            schemas[prefix] = {}
            return schemas[prefix]

    _schemas = {}

    def _eval_annotations(cls, annotations):
        annotations = annotations.copy()
        locls = cls.__dict__
        globls = sys.modules[cls.__module__].__dict__
        for k, v in annotations.items():
            if isinstance(v, str):
                v = eval(v, globls, locls)
                if not isinstance(v, type):
                    raise TypeError(f'`{v}` must evaluate to a type')
                annotations[k] = v
        return annotations

    def __repr__(cls):
        d = {k: v.default for k, v in cls._schema.items()}
        return f'<{cls.__name__}: {d}>'


class ConfigSchema(metaclass=ConfigSchemaMeta, prefix=""):
    '''
    Class representing the valid options in a Carthage Configuration schema

    Typical usage::

        class  VmwareConfig(ConfigSchema, prefix = "vmware"):

            #: Which vMware datacenter should be used?
            datacenter: str

        class VmwareHardware(ConfigSchema, prefix = "vmware.hardware"):

            memory: int = 128 #: memory In megabytes
            paravirt: bool = False
    '''

    class Item:

        '''
        Represents an item in a configuration schema
'''

        __slots__ = ('name', 'type', 'default', 'key')

        def __init__(self, name, type_, default):
            if isinstance(type_, types.GenericAlias):
                type_ = type_.__origin__
            assert isinstance(type_, type), f'{name} config key must be declared with a type not {type_}'
            if type_ is bool:
                from .types import ConfigBool
                type_ = ConfigBool
            elif type_ is str:
                from .types import ConfigString
                type_ = ConfigString
            self.name = name
            self.type = type_
            self.default = default
            self.key = config_key(name)

        def __repr__(self):
            return f'ConfigSchema.Item("{self.name}", {self.type.__name__}, {repr(self.default)})'

        def resolve(self, injector):
            "Return the value of this item resolved against the given injector"
            try:
                res = injector.get_instance(self.key)
                return res
            except InjectorClosed:
                # We may miss substitutions, but this is the best we can do if the injector is closed
                return self.default
                return res
            except (KeyError):
                if self.default is None:
                    return None
                try:
                    with injection_failed_unlogged():
                        res = injector(self.type, self.default)
                    return res
                except Exception as e:
                    raise ConfigResolutionFailed(self.name, self.default) from None


@inject(injector=Injector)
class ConfigAccessor:

    def __init__(self, injector, prefix):
        if not (prefix == "" or prefix.endswith('.')):
            prefix = prefix + '.'
        self._injector = injector
        self._prefix = prefix
        try:
            self._schema = ConfigSchema._schemas[prefix.rstrip('.')]
        except KeyError:
            raise KeyError(f'{prefix} is not a valid configuration prefix') from None

    def __getattr__(self, k):
        if k.startswith('_'):
            raise AttributeError
        try:
            return self._schema[k].resolve(self._injector)
        except KeyError:
            if self._prefix + k in ConfigSchema._schemas:
                try:
                    return self._injector.get_instance(config_key(self._prefix + k))
                except KeyError:
                    return self._injector(ConfigAccessor, prefix=self._prefix + k)
            raise AttributeError(f'{self._prefix}{k} is not a valid configuration key') from None

    def __setattr__(self, k, v):
        if k.startswith('_'):
            return super().__setattr__(k, v)
        if not hasattr(self, k):
            raise AttributeError("{} is not a configuration key".format(self._prefix + k))
        if isinstance(v, dict):
            raise ValueError("You cannot set a configuration key to a dictionary")
        self._injector.replace_provider(config_key(self._prefix + k), v)

    def _dictify(self, include_defaults=False):
        from .types import ConfigBool
        d = {}
        for k, schema_item in self._schema.items():
            def_v = schema_item.default
            try:
                v = getattr(self, k)
            except (ConfigResolutionFailed, InjectionFailed):
                v = "<resolution failed>"
            if include_defaults or (v != def_v):
                for t in (float, int, str):
                    # bool is an int but we want to handle that below
                    if isinstance(v, t) and not isinstance(v, (bool, ConfigBool)):
                        v = t(v)  # Remove any ConfigValue class
                        break
                else:
                    if isinstance(v, ConfigBool):
                        v = bool(v)
                    elif isinstance(v, (tuple, list)):
                        v = list(v)
                    elif v is None or isinstance(v, bool):
                        # just fine as is
                        pass
                    elif isinstance(v, (IPv4Address, IPv4Network, IPv6Address, IPv6Network, Path)):
                        v = str(v)
                    else:
                        raise TypeError(f'{v} is a {type(v)} which will not work so well saved to YAML')

                d[k] = v

        for k in ConfigSchema.subsections(self._prefix):
            d_subsection = getattr(self, k)._dictify(include_defaults=include_defaults)
            if d_subsection or include_defaults:
                d[k] = d_subsection
        return d

    def __getstate__(self):
        return self._dictify(True)

    def __repr__(self):
        return f'<{self.__class__.__name__} overrides: {self._dictify()}>'
