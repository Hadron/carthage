# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import dataclasses
import typing
from ..dependency_injection import *
from .base import NetworkInterface, logger, NetworkLink, BridgeNetwork, VethInterface
from .. import sh


@dataclasses.dataclass
class NetworkNamespace:

    name: str
    network_links: typing.Dict[str, NetworkLink]

    def __post_init__(self):
        logger.debug("Bringing up network namespace for %s", self.name)
        try:
            sh.ip(
                "netns", "add",
                self.name)
        except sh.ErrorReturnCode_1:  # link exists
            sh.ip("netns", "delete", self.name)
            sh.ip("netns", "add", self.name)
        self.closed = False

    async def start_networking(self):
        for interface, link in self.network_links.items():
            net = await link.instantiate(BridgeNetwork)
            veth = net.add_veth(link, self)

    def close(self):
        if self.closed:
            return
        self.closed = True
        logger.info("Deleting network namespace %s", self.name)
        try:
            sh.ip("netns", "delete", self.name)
        except sh.ErrorReturnCode:
            logger.exception("Error deleting network namespace")
