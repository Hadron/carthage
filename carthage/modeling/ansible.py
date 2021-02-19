from .base import MachineModel
from carthage import Machine
from ..ansible import AnsibleGroupPlugin, AnsibleHostPlugin
from ..dependency_injection import *

class ModelingGroupPlugin(AnsibleGroupPlugin):

    name = 'modeling'
    
    async def groups_for(self, m):
        if hasattr(m, 'model'):
            return getattr(m.model, 'ansible_groups', [])
        return []

    async def group_info(self):
        return {}

class ModelingHostPlugin(AnsibleHostPlugin):

    name = 'modeling'

    async def host_vars(self, m:Machine):
        try:
            model = m.model
        except: return {}
        return getattr(model, 'ansible_vars', {})

def enable_modeling_ansible(injector: Injector):
    injector.add_provider(InjectionKey(AnsibleGroupPlugin, name ='modeling'), ModelingGroupPlugin)
    injector.add_provider(InjectionKey(AnsibleHostPlugin, name='modeling'), ModelingHostPlugin)
    
