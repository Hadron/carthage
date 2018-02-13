# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, functools, inspect, pytest

def async_test(t):
    sig = inspect.signature(t)
    if 'loop' not in sig.parameters:
        raise TypeError('The test must take a loop fixture')
    @functools.wraps(t)
    def wrapper(loop, *args, **kwargs):
        kwargs['loop'] = loop
        task = asyncio.ensure_future(t(*args, **kwargs), loop = loop)
        done, pending = loop.run_until_complete(asyncio.wait([task], timeout = 30, loop = loop))
        if pending: raise  TimeoutError
        return task.result()
    return wrapper


