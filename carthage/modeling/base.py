from .implementation import *
from carthage.dependency_injection import * #type: ignore
import typing

@inject(injector = Injector)
class InjectableModel(Injectable, metaclass = InjectableModelType):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for k,info in self.__class__.__initial_injections__.items():
            v, options = info
            try:
                self.injector.add_provider(k, v, **options)
            except Exception as e:
                raise RuntimeError(f'Failed registering {v} as provider for {k}') from e
