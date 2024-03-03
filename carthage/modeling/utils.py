# Copyright (C) 2018, 2019, 2020, 2021, 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import typing


def combine_mro(
        base: typing.Union[type, typing.Sequence[type]],
        subclass: type, attribute: str,
        add: typing.Callable,
        state):
    # for all members of the mro of base that are subclasses of
    # *subclass*, run ``add(mro_member, getattr(mro_member,
    # attribute), state)`` *base* may be a sequence This is intended
    # for collecting together attributes like classes_to_inject or
    # namespace_filters that are manually set on a class and need to
    # be combined together.  Currently, if there are conflicts (as in
    # multiple classes setting the same key in a mapping), the
    # visitation order is wrong and the wrong value will survive.
    #
    # This function is not appropriate for values like
    # __initial_injections__, __container_propagations__, or
    # __transclusions__ that are automatically maintained and built up
    # as the inheritance tree is constructed.  For one thing, it is
    # only necessary to descend one level since the earlier levels
    # should have already accumulated together.  Also, typically,
    # those attributes are attributes of say ModelingContainer or
    # InjectableModelType instances rather than of say
    # ModelingContainer subclasses, and so the subclass check is
    # guaranteed to fail.
    if isinstance(base, type):
        base = [base]
        mro_set = set()
        mro = []
        for b in base:
            if b not in mro_set and issubclass(b, subclass):
                mro_set.add(b)
                mro.append(b)
                try:
                    res = getattr(b, attribute)
                    add(b, res, state)
                except AttributeError:
                    pass
            for m in b.__mro__:
                if m in mro_set:
                    continue
                if not issubclass(m, subclass):
                    continue
                mro_set.add(m)
                mro.append(m)
                try:
                    res = getattr(m, attribute)
                except AttributeError:
                    continue
                add(m, res, state)


def combine_mro_list(base, subclass, attribute):
    def add(m: type, res: list, state):
        for l in res:
            if l not in state:
                state.append(l)
    state = []
    combine_mro(base, subclass, attribute, add, state)
    return state


def combine_mro_mapping(base, subclass, attribute) -> typing.Dict[str, typing.Any]:
    def add(m, res, state):
        for k, v in res.items():
            if k not in state:
                state[k] = v
    state: typing.Dict[str, typing.Any] = {}
    combine_mro(base, subclass, attribute, add, state)
    return state


__all__ = [
    'combine_mro_list',
    'combine_mro_mapping'
]


def setattr_default(obj, a: str, default, inherited_ok=False):
    if inherited_ok:
        has_attr = hasattr(obj, a)
    else:
        has_attr = a in obj.__dict__
    if not has_attr:
        setattr(obj, a, default)


__all__ += ['setattr_default']


def gather_from_class(self, *keys, mangle_name=True):
    '''
    :param mangle_name: If true, and name is not in class, set name from __name__
    '''

    d: dict = {}
    if isinstance(self, type):
        cls = self
    else:
        cls = self.__class__
    for k in keys:
        try:
            d[k] = getattr(cls, k)
        except AttributeError:
            if k == 'name' and mangle_name:
                d['name'] = cls.__name__.lower()
    return d


__all__ += ['gather_from_class']


def key_from_injector_access(*accesses):
    from .decorators import injector_access
    result = []
    for k in accesses:
        if isinstance(k, injector_access):
            k = k.key
        elif isinstance(k, list):
            k = [x.key if isinstance(x, injector_access) else x for x in k]
        elif  hasattr(k, '__provides_dependencies_for__'):
            k = k.__provides_dependencies_for__[0]
        result.append(k)
    return result


__all__ += ['key_from_injector_access']


def fixup_dynamic_name(n):
    return n.replace('-', '_')


__all__ += ['fixup_dynamic_name']
