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
try:
    # Setting _async to true doesn't do much except it tends to override _bg, and too much of our code gets confused by that.
    _sh_context = _sh.bake(_return_cmd=True, _bg=True, _bg_exc=False)
    async def test_return_cmd():
        import warnings
        c = await _sh_context.ls(_async=True, _return_cmd=True)
        if not isinstance(c, _sh.RunningCommand):
            warnings.warn('This sh is too old to properly handle _async _return_cmd=True')
            return True
        try:
            await _sh_context.false(_return_cmd=True, _async=True)
            warnings.warn('sh drops exceptions on await')
            return True
        except _sh.ErrorReturnCode:
            return False
        
        return False
    force_override_await = _asyncio.get_event_loop().run_until_complete(test_return_cmd())
except AttributeError:
    _sh_context = _sh(_bg=True, _bg_exc=False)
    force_override_await = False

def __getattr__(name):
    val = getattr(_sh_context, name)
    globals()[name] = val
    return val

def running_command_await(self):
    loop = _asyncio.get_event_loop()
    res =  yield from loop.run_in_executor(None, self.wait)
    return res

if not hasattr(_sh.RunningCommand, '__await__') or force_override_await:
    _sh.RunningCommand.__await__ = running_command_await
del running_command_await
del force_override_await
