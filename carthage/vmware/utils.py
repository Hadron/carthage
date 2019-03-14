import asyncio, time

def wait_for_task(task):
    loop = asyncio.get_event_loop()
    def callback():
        while task.info.state not in ('success', 'error'):
            time.sleep(0.2)
        if task.info.state == 'error':
            raise task.info.error
    return loop.run_in_executor(None, callback)
