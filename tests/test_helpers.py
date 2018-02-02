import asyncio, functools, inspect, pytest

def async_test(t):
    sig = inspect.signature(t)
    if 'loop' not in sig.parameters:
        raise TypeError('The test must take a loop fixture')
    @functools.wraps(t)
    def wrapper(loop, *args, **kwargs):
        kwargs['loop'] = loop
        task = asyncio.ensure_future(t(*args, **kwargs), loop = loop)
        done, pending = loop.run_until_complete(asyncio.wait([task], timeout = 0.7, loop = loop))
        if pending: raise  TimeoutError
        return task.result()
    return wrapper


