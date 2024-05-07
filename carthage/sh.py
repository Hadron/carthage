# Copyright (C) 2023, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.


import asyncio as _asyncio
import sh as _sh

'''
Monkey patch the sh module to include __await__.
Also, by default for commands retrieved through this module set _bg=True and _bg_exc=False
'''
_sh_context = _sh(_bg=True, _bg_exc=False)

def __getattr__(name):
    val = getattr(_sh_context, name)
    globals()[name] = val
    return val

def running_command_await(self):
    loop = _asyncio.get_event_loop()
    res =  yield from loop.run_in_executor(None, self.wait)
    return res

if not hasattr(_sh.RunningCommand, '__await__'):
    _sh.RunningCommand.__await__ = running_command_await
del running_command_await
