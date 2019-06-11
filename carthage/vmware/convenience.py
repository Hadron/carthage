import asyncio
import itertools
import asyncio, yaml
import urllib3
import carthage, carthage.utils

from pyVmomi import vim, vmodl

from carthage import base_injector, inject, AsyncInjector, ConfigLayout, ansible
from carthage.config import ConfigSchema
from carthage.dependency_injection import *
from carthage.ssh import SshKey
from carthage.console import *
from carthage.network import *
from carthage.vmware import *
from carthage.vmware.network import *
from carthage.vmware.folder import *
from carthage.vmware.inventory import *
from carthage.vmware.authorization import *
from carthage.vmware.datastore import *
from carthage.vmware.datacenter import VmwareDatacenter
from carthage.vmware.cluster import VmwareCluster
from carthage.vmware.utils import wait_for_task
from carthage.utils import *

def carthage_load_config(s):
    base_injector(ConfigLayout).load_yaml(s)
