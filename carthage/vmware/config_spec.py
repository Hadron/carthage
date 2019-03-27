import weakref
from ..dependency_injection import *
from . import inventory
from pyVmomi import vim

class ConfigSpecMeta(type):

    def __repr__(self):
        stage = getattr(self, 'stage_for', None)
        if stage is not None:
            stage = stage.__name__
        else:
            stage = "<abstract>"
            
        return f'<{stage} config stage order={self.order} for {self.stage_for.__name__}>'
    
class ConfigSpecStage(metaclass = ConfigSpecMeta):

    '''
    Represents a Stage in building a Vmware configuration.  A number of factors influence which items need to be set in a configuration:

    * For creating an object all items must be set

    * When adding a disk or network to a VM, typically only those items are needed.

    * When cloning a VM, we want to remove all network adapters.

    Beyond that it may be necessary to collect information from parts of the config even if those are not being updated.  As an example, to add a disk, you need the SCSI controllerkey.  So even when  a config item is not edited, it may need to be examined.

    This class represents code that will be run against the config.  The *order* class variable determines which order will be used.

    .. _bag_keys:
    Bag Keys
    ________

    mode
        What sort of configuration mode is being used.  The following modes are defined:

        create
            The initial configuration is being created

        clone
            A VM is being cloned

        clone_disk
            A configspec is being constructed for resizing and adjusting cloned disks.  Only the devices entry from this ConfigSpec will actually be used.

    scsi_key
        SCSI controller key

        
      
    '''
    
    def __init_subclass__(cls, stage_for, order = 100, mode = 'create'):
        '''
        :param stage_for: Which :class:`~inventory.VmwareSpecifiedObject` is this a configuration stage for?

        :param order: When should this stage be run?  Lower numbers are run first.
        :param mode: Either a single mode or a tuple of modes that this config spec works for, or True if for all modes.

'''
        cls.order = order
        if stage_for is None: return
        cls.stage_for = weakref.proxy(stage_for)
        cls.mode = mode
        assert issubclass(stage_for, inventory.VmwareSpecifiedObject)
        if 'config_stages' not in stage_for.__dict__:
            setattr(stage_for, 'config_stages', stage_for.config_stages.copy())
        stage_for.config_stages.append(cls)
        stage_for.config_stages.sort(key = lambda c: c.order)

    def __init__(self, obj, bag):
        self.obj = obj
        self.mob = obj.mob
        #: A namespace containing keys shared between stages so that sthages can communicate.
        #:
        #: ..seealso:: `bag_keys`
        self.bag = bag
        if self.mob:
            self.oconfig = self.mob.config
        else:
            self.oconfig = None

        
    def apply_config(self, config):
        pass

    def __repr__(self):
        return f'<{self.__class__.__name__} config stage for {self.obj} order={self.order}>'
    
    

class DeviceSpecStage(ConfigSpecStage, stage_for = None):

    def __init_subclass__(cls, stage_for, dev_classes,
                          **kwargs):
        kwargs.setdefault('mode', ('create', 'reconfig'))
        if not isinstance(dev_classes, (tuple, list)):
            dev_classes = (dev_classes,)
        cls.dev_classes = dev_classes
        super().__init_subclass__(stage_for = stage_for, **kwargs)

    @inject(ainjector = AsyncInjector)
    async def apply_config(self, config, *, ainjector):
        if self.oconfig:
            for d in self.oconfig.hardware.device:
                if not isinstance(d, self.dev_class): continue
                res = await ainjector(self.filter_device,d)
                if res is True: continue
                if isinstance(res,vim.vm.device.VirtualDevice):
                    spec = vim.vm.device.VirtualDeviceSpec()
                    spec.fileOperation = getattr(self, 'file_operation', None)
                    spec.device = res
                    spec.operation = 'edit'
                    config.deviceChange.append(spec)
                elif res is False:
                    spec = vim.vm.device.VirtualDeviceSpec()
                    spec.device = d
                    spec.operation = 'remove'
                    config.deviceChange.append(spec)
                else: raise ValueError(f'Don\'t know how to handle filter_device of {res}')
        # Handle new devices
        for d in await ainjector(self.new_devices, config):
            spec = vim.vm.device.VirtualDeviceSpec()
            spec.device = d
            spec.operation = 'add'
            spec.fileOperation = getattr(self, 'file_operation', None)
            config.deviceChange.append(spec)

    def filter_device(self, d):
        '''
Return *True* to keep the device.

        Return *False* to drop the device from the configuration

        Return A device to change the device

        '''
        return True

    def new_devices(self, config):
        return []
    
__all__ = ('ConfigSpecStage', 'DeviceSpecStage')
