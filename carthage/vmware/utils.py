import asyncio, time

def wait_for_task(task):
    loop = asyncio.get_event_loop()
    ''' Returns a future that  when done indicates the task is complete.  Note that while this is not async, it should be treated as if it is because it returns a future.
    Example usage::
    
        await wait_for_task(task)

    '''
    # We use a separate thread to avoid blocking the async loop on http round trips to look up task state
    def callback():
        while task.info.state not in ('success', 'error'):
            time.sleep(0.2)
        if task.info.state == 'error':
            raise task.info.error
    return loop.run_in_executor(None, callback)

