# Copyright (C) 2018, 2019, 2020, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import time
import pyVim.task


def wait_for_task(task):
    loop = asyncio.get_event_loop()
    ''' Returns a future that  when done indicates the task is complete.  Note that while this is not async, it should be treated as if it is because it returns a future.
    Example usage::

        await wait_for_task(task)

    '''
    # We use a separate thread to avoid blocking the async loop on http round trips to look up task state
    def callback():
        pyVim.task.WaitForTask(task)
        if task.info.state == 'error':
            class TaskError(type(task.info.error)):
                def __str__(self):
                    return f'Error: {super().__str__()}; info: {self.info_str}'

                def __init__(self, task):
                    self.__dict__.update(task.info.error.__dict__)
                    self.__dict__['info_str'] = str(task.info)
                    self.__dict__['task'] = task
                    task.info.__dict__['error'] = None
            raise TaskError(task)
    return loop.run_in_executor(None, callback)


class TaskIgnoreErrors:

    def __init__(self, *types):
        self.types = types

    def __enter__(self):
        print('enter types', self.types)
        return self

    def __exit__(self, etype, einstance, etraceback):
        if etype is not None:
            for t in self.types:
                if issubclass(etype, t):
                    return True
