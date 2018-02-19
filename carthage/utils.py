# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, functools

async def possibly_async(r):
    '''If r is a coroutine, await it.  Otherwise return it.  Used like the
    following:

        return await possibly_async(self.check_volume())

    check_volume can now optionally be declared async
    '''
    if asyncio.iscoroutine(r):
        return await r
    else:
        return r
    

class memoproperty:
    "A property that only supports getting and that stores the result the first time on the instance to avoid recomputation"


    def __init__(self, fun):
        functools.update_wrapper(self, fun)
        self.fun = fun
        self.name = fun.__name__

    def __get__(self, instance, owner):
        if instance is None: return self
        #Because we don't define set or del, we should not be called
        #if name is already set on instance.  So if we set name we
        #will be bypassed in the future
        res = self.fun(instance)
        setattr(instance, self.name, res)
        return res
    
