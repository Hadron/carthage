# Copyright (C) 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio


class Trigger:

    '''An awaitable that can be externally triggered and that by default asserts if not triggered when used as a context manager
'''

    def __init__(self):
        self.future = asyncio.get_event_loop().create_future()
        self.awaited = False

    def __await__(self):
        yield from self.future
        self.awaited = True

    def assert_triggered(self):
        assert self.future.done()
        self.future.result()
        self.awaited = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_val:
            if not self.future.done():
                self.future.set_exception(exc_val)
            return False
        assert self.awaited, "This trigger was never awaited"
        return False

    def trigger(self, result=True):
        self.future.set_result(True)
