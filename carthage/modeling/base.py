# Copyright (C) 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

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

class Enclave(InjectableModel, metaclass = ModelingContainer):

    domain: str

    @classmethod
    def our_key(self):
        return InjectionKey(Enclave, domain=self.domain)

    __all__ = ['InjectableModel', 'Enclave']
    
