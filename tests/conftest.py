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
        
            
