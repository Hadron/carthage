import asyncio

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
    
