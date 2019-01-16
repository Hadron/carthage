# Copyright (C) 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import sys

def get_tb(e):
    if isinstance(e, BaseException):
        return e.__traceback__
    return e

def iter_tb(tb):
    while tb.tb_next:
        yield tb
        tb = tb.tb_next
        
def filter_before_here(e):
    tb = get_tb(e)
    frame = sys._getframe(1)
    for i in iter_tb(tb):
        if i.tb_frame is frame:
            if isinstance(e, BaseException):
                e.__traceback__ = i
            return tb
    return tb

__all__ = ('filter_before_here', )
