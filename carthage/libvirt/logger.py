# Copyright (C) 2025 Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import logging

logger = logging.getLogger("carthage.libvirt")

def get_child(name: str):
    return logger.getChild(name)

__all__ = ["get_child"]
