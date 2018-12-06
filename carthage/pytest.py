# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, functools, inspect, pytest

'''
Decorators and functions for use with Carthage.

This module is typically used alongside the :ref:`carthage.pytest_plugin` Pytest plugin.  This module contains decorators like :funcref:`async_test` and that plugin provides fixtures and hooks needed for these items to work.  Supported functionality includes:

* *asyncio* tests that support :ref:`carthage.inject` style dependency injection

'''

def async_test(t):
    '''A decorator for wrapping a test. *t* is expected to be a coroutine
    and will be run inside an event loop.  The test may take either
    Carthage style dependencies using the :funcref:`carthage.inject`
    decorator or Pytest style fixtures as function arguments.  Any
    Carthage style injected item will be removed from the list of
    Pytest style injectors.

    '''
    # This is messy because Pytest's introspection logic does not
    # respect inspect.Signature.  So we don't actually include the
    # _wrapped_ item and explicitly mark tests with pytest.usefixture
    # to indicate which fixtures are required.  However, we need to
    # depend on a pytest_collect_modifyitems hook in
    # carthage.pytest_plugin to make the fixtures available to the
    # function.
    
    sig = inspect.signature(t)
    orig_loop = True
    orig_ainjector = True
    @functools.wraps(t)
    def wrapper(loop, *args,  **kwargs):
        if orig_loop:
            kwargs['loop'] = loop
        ainjector = kwargs.get('ainjector', None)
        if ainjector is None:
            task = asyncio.ensure_future(t(*args, **kwargs), loop = loop)
        else:
            if not orig_ainjector:
                del kwargs['ainjector']
            task = asyncio.ensure_future(ainjector(t, *args, **kwargs), loop = loop)
        done, pending = loop.run_until_complete(asyncio.wait([task], timeout = 40, loop = loop))
        if pending: raise  TimeoutError
        return task.result()
    params = list(sig.parameters.values())
    try:
        params = list(filter( lambda p: p.name not in t._injection_dependencies, params))
    except AttributeError: pass # no @inject call
    param_names = set(p.name for p in params)
    if 'loop' not in param_names:
        orig_loop = False
        params.append(inspect.Parameter(name = "loop", kind = inspect._KEYWORD_ONLY))
    if 'ainjector' not in param_names :
        orig_ainjector = False
        if  hasattr(t, '_injection_dependencies'):
            params.append(inspect.Parameter(name = "ainjector", kind = inspect._KEYWORD_ONLY))
    sig = sig.replace(parameters = params)
    wrapper.__signature__ = sig
    pytest.mark.usefixtures(*sig.parameters.keys())(wrapper)
    del wrapper.__dict__['__wrapped__']
    wrapper.place_as = t
    return wrapper

__all__ = 'async_test'.split()
