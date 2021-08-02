# Copyright (C) 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3

from carthage import *
from carthage.modeling import *

class layout(CarthageLayout):

    class it_com(Enclave):

        domain = "it.com"

        class server(MachineModel): pass

    class bank_com(Enclave):

        domain = "bank.com"

        class server(MachineModel): pass

        
