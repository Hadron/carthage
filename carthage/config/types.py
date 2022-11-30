# Copyright (C) 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import os.path
from ..dependency_injection import inject, InjectionKey, Injectable, Injector
from .layout import ConfigLayout


def getattr_path(o, attrs):
    attrs_iter = attrs
    try:
        while attrs_iter:
            left, sep, attrs_iter = attrs_iter.partition('.')
            o = getattr(o, left)
            if not attrs_iter:
                return o
    except AttributeError:
        raise AttributeError(f'Unable to find {attrs}') from None


@inject(
    injector=Injector)
class ConfigString(str):

    '''A string that substitutes

    * ``{key}`` with the result of that config key

    * ``{plugin:selector}`` with the lookup of *selector* using the *plugin* :ref:`.ConfigLookupPlugin`

    Backslash scapes the next character always; ``${var}`` is reserved for :ref:`ConfigPath` use.

    '''

    @classmethod
    def parse(cls, s, config, injector):
        def tok(i, awaiting_brace):
            # takes an iterator to consume input characters
            lastch = None
            for c in i:
                if lastch == '\\':
                    yield c
                elif c == '\\':
                    # one backslash always eats itself.
                    pass
                elif c == '{':
                    if lastch == "$":
                        yield '{'
                        yield from tok(i, True)
                        yield "}"
                    else:
                        yield from iter(cls.subst(
                            "".join(tok(i, True)),
                            config=config,
                            injector=injector
                        ))
                elif c == '}':
                    if awaiting_brace:
                        return  # end of inner token
                    else:
                        raise ValueError(f"Unbalanced closing brace in `{s}'")
                else:
                    yield c
                lastch = c

            if awaiting_brace:
                raise ValueError(f"Missing right brace in `{s}'")

        return "".join(tok(iter(s), False))

    @staticmethod
    def subst(s, *, config, injector):
        plugin, sep, selector = s.partition(':')
        if sep == '':
            return getattr_path(config, s)
        else:
            try:
                plugin = injector.get_instance(InjectionKey(ConfigLookupPlugin, name=plugin))
            except KeyError:
                raise KeyError(f'Config lookup plugin {plugin} not found') from None
            return plugin(selector)

    def __new__(cls, s, *, injector):
        config = injector(ConfigLayout)
        return str.__new__(str, cls.parse(os.path.expandvars(s), config, injector))


@inject(
    injector=Injector)
class ConfigPath(ConfigString):

    def __new__(cls, s, *, injector):
        return super().__new__(ConfigString, os.path.expanduser(s),
                               injector=injector)


class ConfigBool:

    "A type that can be subtyped to be injectable used instead of bool"

    def __new__(cls, val):
        return bool(val)


class ConfigLookupPlugin (Injectable):

    '''
    An abstract class representing an interface that can be used to look up information in an external store to fill in config values.  Used in config schemas like::

        password: {plugin:where_to_find_password}

    Registered like::

        ConfigPasswordPlugin.register(base_injector, "password")

'''

    @classmethod
    def register(cls, injector, name):
        injector.add_provider(InjectionKey(ConfigLookupPlugin, name=name), cls)

    def __call__(self, selector):
        '''
        return the value looked up using the given selector.
Must be overridden; this is abstract.
'''
