# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import random

def random_mac_addr():
    mac = [random.randint(0,255) for x in range(6)]
    mac[0] &= 0xfc #Make it locally administered
    macstr = [format(m, "02x") for m in mac]
    return ":".join(macstr)

__all__ = ['random_mac_addr']
