# Copyright (C) 2018, 2019, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import yaml
from .dependency_injection import inject, Injectable

class ConfigSection:

    def add_config(self, c):
        '''
        Add additional config settings.

        :param: c
            A dictionary of key-value pairs.  The keys will become valid config settings with the value being the initial default.  If the value is a dict, it will be converted to a :class:`ConfigSection`.

'''
        for k,v in c.items():
            if hasattr(self, k):
                orig_val = getattr(self, k)
                if isinstance(orig_val, ConfigSection):
                    if isinstance(v, dict):
                        orig_val.add_config(v)
                    else:
                        raise ValueError("{k} needs to be a dictionary to fit into existing ConfigSection".format(k))
                elif isinstance(v, dict):
                    cs = ConfigSection()
                    cs.add_config(v)
                    setattr(self, k, cs)
                else: setattr(self, k, v)
                
    def _copy(self):
        new = self.__class__()
        for k,v in self.__dict__.items():
            d = None
            if isinstance(v, ConfigSection):
                d = v.__dict__
            elif isinstance(v, dict):
                d = v
            if d is not None:
                cs = ConfigSection()
                cs.__dict__.update(d)
                setattr(new, k, cs)
            else: setattr(new, k, copy.copy(v))
        return new

    def _load(self, d):
        for k,v in d.items():
            try:
                v_orig = getattr(self, k)
                if hasattr(v_orig, '__get__'):
                    raise ValueError('{} cannot be set'.format(k))
                if isinstance(v_orig, ConfigSection):
                    if not isinstance(v, dict):
                        raise ValueError("{} should be a dictionary".format(k))
                    v_orig._load(v)
                else:
                    setattr(self, k, v)
            except AttributeError:
                raise AttributeError("{} is not a config attribute".format(k)) from None
        
                
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
        self._load(d)


    @classmethod
    def add_config(cls, c):
        #To work around the fact that the master ConfigLayout is
        #instantiated late in the game, but ConfigSections are
        #instantiated as defined.
        ConfigSection.add_config(cls, c)
        
