# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import dataclasses
from .base import NetworkLink, VlanList
'''
    Switch Utilities
    ================

    This Module provides utilities to  ease in converting Carthage :class:`NetworkLink` into switch configuration

'''
__all__ = []


def links_by_port_channel(links: list[NetworkLink]):
    result: dict[str, list[NetworkLink]] = dict()
    for l in links:
        pc_member = getattr(l, 'portchannel_member', None)
        if pc_member:
            result.setdefault(pc_member, [])
            result[pc_member].append(l)
    return result


__all__ += ['links_by_port_channel']


def link_vlan_config(link):
    '''Select a link from which we can get VLAN configuration.

    Try the link itself.  If the link isa member of a bond, try that.
    Also try the same for the other side.  In practice, VLAN
    configuration for bonds probably will not be on the switch side of
    the links because the switch side of a bond link cannot be
    represented when it includes multiple switches.
'''
    def try_link(l):
        if l in already_tried:
            return
        to_try.append(l)
    to_try = [link]
    already_tried = set()
    while to_try:
        link = to_try.pop(0)
        already_tried.add(link)
        if link.allowed_vlans:
            link.allowed_vlans = VlanList.canonicalize(link.allowed_vlans, link)
            if link.untagged_vlan is None and link.net.vlan_id:
                link.untagged_vlan = link.net.vlan_id
            return link
        for member_of_link in link.member_of_links:
            if member_of_link.local_type == 'bond':
                try_link(member_of_link)
        if link.other:
            try_link(link.other)
    return None


__all__ += ['link_vlan_config']


def link_collect_nets(link):
    def try_link(l):
        if l in links:
            return
        to_try.append(l)
    to_try = [link]
    links = set()
    nets = set()
    while to_try:
        link = to_try.pop(0)
        links.add(link)
        if link.net not in nets:
            yield link.net
            nets.add(link.net)
        for l in link.member_of_links:
            try_link(l)
        if link.other:
            try_link(link.other)


def link_collect_vlans(link):
    return {net.vlan_id for net in link_collect_nets(link) if net.vlan_id is not None}


def cisco_vlan_list(vlan_list):
    result = []
    for i in vlan_list:
        if isinstance(i, int):
            result.append(str(i))
        elif isinstance(i, slice):
            result.append(f'{i.start}-{i.end}')
    return ",".join(result)


__all__ += ['cisco_vlan_list']
