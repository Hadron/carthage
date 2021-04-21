import random

def random_mac_addr():
    mac = [random.randint(0,255) for x in range(6)]
    mac[0] &= 0xfc #Make it locally administered
    macstr = [format(m, "02x") for m in mac]
    return ":".join(macstr)

__all__ = ['random_mac_addr']
