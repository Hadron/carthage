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


def filter_chatty_modules(e, module_list, level=1):
    '''
    Filter chatty modules from an exception or traceback

    :param e: a :class:`Traceback` or :class:`BaseException` to be filtered in place

    :param module_list: A list of modules that are viewed as too chatty.  At most one consecutive entry from these modules will be retained.

    :param level: How many stack frames to look back for the caller.  Nothing before the caller will be filtered.  If *level* is None, the entire stack is filtered.

    This function first looks and finds the caller *level* levels above this call.

    #. The caller frame and all frames in the exception stack before the caller are left alone.

    #. Any frames that come from files whose modules are in *module_list* are filtered

    #. The filtering stops at the first frame not in *module_list*.

    '''
    tb = get_tb(e)
    if level is not None:
        caller = sys._getframe(level)
        caller_found = False
    else:
        caller_found = True
    module_filenos = frozenset(x.__file__ for x in module_list)
    while not caller_found and tb.tb_next is not None:
        if not caller_found:
            if caller is tb.tb_frame:
                caller_found = True
            else:
                tb = tb.tb_next
        else:  # caller already found
            break
    if caller_found:
        while (tb.tb_next is not None) and tb.tb_next.tb_next is not None:
            if tb.tb_next.tb_frame.f_code.co_filename in module_filenos \
                    and tb.tb_next.tb_next.tb_frame.f_code.co_filename in module_filenos:
                tb.tb_next = tb.tb_next.tb_next
            else:
                tb = tb.tb_next


__all__ = ('filter_before_here', 'filter_chatty_modules')
