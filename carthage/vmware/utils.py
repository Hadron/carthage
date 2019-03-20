# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, time

def wait_for_task(task):
    loop = asyncio.get_event_loop()
    def callback():
        while task.info.state not in ('success', 'error'):
            time.sleep(0.2)
        if task.info.state == 'error':
            raise task.info.error
    return loop.run_in_executor(None, callback)

async def await_task(task):
    while task.info.state not in ('success', 'error'):
        await asyncio.sleep(0.2)
    if task.info.state == 'error':
        raise task.info.error
