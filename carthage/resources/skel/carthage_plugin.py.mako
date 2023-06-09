from . import layout
from carthage import *

@inject(injector = Injector)
def carthage_plugin(injector):
    injector.add_provider(layout.Layout)
