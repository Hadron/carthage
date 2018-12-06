# Copyright (C) 2018, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio, pytest
from carthage.image import image_factory
from carthage import base_injector
from carthage.dependency_injection import AsyncInjector

pytest_plugins = ('carthage.pytest_plugin',)




@pytest.fixture(scope = 'session')
def vm_image( loop):
    ainjector = base_injector(AsyncInjector)
    image = loop.run_until_complete(ainjector(image_factory, name = "base"))
    yield image
    image.close()
    

                

def pytest_runtest_logreport(report):
    with open("out.json", "ta") as f:
        import json
        d = {}
        for k in ('nodeid', 'location', 'keywords', 'outcome', 'longrepr', 'when', 'sections', 'duration'):
            d[k] = getattr(report, k)
        d['longrepr'] = report.longreprtext
        f.write(json.dumps(d))
        from _pytest.runner import TestReport
        TestReport(**d)
        
            
