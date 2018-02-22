import os, os.path
from .dependency_injection import Injector, AsyncInjectable, inject, AsyncInjector
from .config import ConfigLayout
from . import sh
from .utils import possibly_async
import carthage


_task_order = 0
def setup_task(stamp):
    '''Mark a method as a setup task.  Indicate a stamp file to be created
    when the operation succeeds.  Must be in a class that is a subclass of
    SetupTaskMixin.  Usage:

        @setup_task("unpack"
        async def unpack(self): ...
    '''
    def wrap(fn):
        global _task_order
        fn._setup_task_info = (_task_order, stamp)
        _task_order += 1
        return fn
    return wrap

class SkipSetupTask(Exception): pass


class SetupTaskMixin:

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_tasks = sorted(self._class_setup_tasks(),
                                  key = lambda t: t[1]._setup_task_info[0])

    def add_setup_task(self, stamp, task):
        self.setup_tasks.append((stamp, task))

    async def run_setup_tasks(self):
        injector = getattr(self, 'injector', carthage.base_injector)
        ainjector = getattr(self, 'ainjector', None)
        if ainjector is None:
            ainjector = injector(AsyncInjector)
        for stamp, task in self.setup_tasks:
            if not check_stamp(self.stamp_path, stamp):
                try:
                    await ainjector(task)
                    create_stamp(self.stamp_path, stamp)
                except SkipSetupTask: pass

    def _class_setup_tasks(self):
        cls = self.__class__
        meth_names = {}
        for c in cls.__mro__:
            if not issubclass(c, SetupTaskMixin): continue
            for m in c.__dict__:
                if m in meth_names: continue
                meth = getattr(c, m)
                meth_names[m] = True
                if hasattr(meth, '_setup_task_info'):
                    yield meth._setup_task_info[1], getattr(self, m)
                    
            
    
@inject(config_layout = ConfigLayout)
class BtrfsVolume(AsyncInjectable, SetupTaskMixin):

    def __init__(self, config_layout, name, clone_from = None):
        super().__init__()
        self.config_layout = config_layout
        self._name = name
        self._path = os.path.join(config_layout.image_dir, name)
        self.clone_from = clone_from

    @property
    def name(self): return self._name

    @property
    def path(self):
        if self._path is None:
            raise RuntimeError("This volume is closed")
        return self._path

    def __repr__(self):
        return "<BtrfsVolume path={}>".format(self.path)

    def close(self):
        if self._path is None: return
        if self.config_layout.delete_volumes:
            sh.btrfs('subvolume', 'delete', self.path, )
        self._path = None

    def __del__(self):
        self.close()
    
    async def async_ready(self):
        try:
            if os.path.exists(self.path):
                try: sh.btrfs("subvolume", "show", self.path)
                except sh.ErrorReturnCode:
                    raise RuntimeError("{} is not a btrfs subvolume but already exists".format(self.path))
                # If we're here it is a btrfs subvolume
                await possibly_async(self.populate_volume())
                return self
            # directory does not exist
            os.makedirs(os.path.dirname(self.path), exist_ok = True)
            if not self.clone_from:
                await sh.btrfs('subvolume', 'create',
                               self.path,
                               _bg = True, _bg_exc = False)
            else:
                await sh.btrfs('subvolume', 'snapshot', self.clone_from.path, self.path,
                               _bg = True, _bg_exc = False)
            await possibly_async(self.populate_volume())
            return self
        except:
            self.close()
            raise


    async def populate_volume(self):
        "Populate a new volume; called both for cloned and non-cloned volumes"
        await self.run_setup_tasks()
    stamp_path = path
    



@inject(config_layout = ConfigLayout)
class ImageVolume(BtrfsVolume):

    def __init__(self, name, config_layout):
        super().__init__(config_layout = config_layout, name = name)

    @setup_task('unpack')
    async def unpack_container_image(self):
        await sh.tar('--xattrs-include=*.*', '-xpzf',
                     self.config_layout.base_container_image,
                     _cwd = self.path,
                     _bg_exc = False,
                     _bg = True)

    @setup_task('cleanup-image')
    def cleanup_image(self):
        try:         os.unlink(os.path.join(self.path, 'usr/sbin/policy-rc.d'))
        except FileNotFoundError: pass
        try: os.rename(os.path.join(self.path, "sbin/init.dist"),
                  os.path.join(self.path, "sbin/init"))
        except FileNotFoundError: pass
        

def create_stamp(path, stamp):
    with open(os.path.join(path, ".stamp-"+stamp), "w") as f:
        pass
    

def check_stamp(path, stamp, raise_on_error = False):
    if not os.path.exists(os.path.join(path,
                                       ".stamp-"+stamp)):
        if raise_on_error: raise RuntimeError("Stamp not available")
        return False
    return True
    

__all__ = ('BtrfsVolume', 'ImageVolume', 'SetupTaskMixin')
