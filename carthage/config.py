import yaml
from .dependency_injection import inject, Injectable

class ConfigLayout(Injectable):

    image_dir = "/srv/images/test"
    vm_image_dir = "/srv/images/test/vm"
    vm_image_size = 20000000000
    
    base_container_image = "/usr/share/hadron-installer/hadron-container-image.tar.gz"
    base_vm_image = "/usr/share/hadron-installer/direct-install-efi.raw.gz"
    container_prefix = 'carthage-'
    state_dir ="/srv/images/test/state"
    min_port = 9000
    max_port = 9500
    hadron_operations = "/home/hartmans/hadron-operations"
    delete_volumes = False

    def load_yaml(self, y):
        d = yaml.load(y)
        assert isinstance(d,dict)
        for k,v in d.items():
            try:
                v_orig = getattr(self, k)
                if hasattr(v_orig, '__get__'):
                    raise ValueError('{} cannot be set'.format(k))
                setattr(self, k, v)
            except AttributeError:
                raise AttributeError("{} is not a config attribute".format(k)) from None
            
