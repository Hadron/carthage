import asyncio, logging, re, sys
from .dependency_injection import inject, AsyncInjectable, InjectionKey, Injector, AsyncInjector
from .image import BtrfsVolume, ImageVolume, SetupTaskMixin
from . import sh, ConfigLayout

logger = logging.getLogger('carthage.container')


class ContainerRunning:

    async def __aenter__(self):
        self.container.with_running_count +=1
        if self.container.running:
            return
        try:
            await self.container.start_container()
            return

        except:
            self.container.with_running_count -= 1
            raise

    async def __aexit__(self, exc, val, tb):
        self.container.with_running_count -= 1
        if self.container.with_running_count <= 0:
            self.container_with_running_count = 0
            await self.container.stop_container()


    def __init__(self, container):
        self.container = container

container_image = InjectionKey('container-image')
container_volume = InjectionKey('container-volume')

@inject(image = container_image,
        loop = asyncio.AbstractEventLoop,
        config_layout = ConfigLayout,
        injector = Injector)
class Container(AsyncInjectable, SetupTaskMixin):

    def __init__(self, name, *, config_layout, image, injector, loop):
        super().__init__(injector = injector)
        self.loop = loop
        self.process = None
        self.name = name
        self.injector = Injector(injector)
        self.image = image
        self.config_layout = config_layout
        self.with_running_count = 0
        self.running = False
        self._operation_lock = asyncio.Lock()
        self._out_selectors = []
        self._done_waiters = []
        self.container_running = ContainerRunning(self)
        

    async def async_ready(self):
        ainjector = self.injector(AsyncInjector)
        try: vol = self.injector.get_instance(container_volume)
        except KeyError:
            vol = await ainjector(BtrfsVolume,
                              clone_from = self.image,
                              name = "containers/"+self.name)
            self.injector.add_provider(container_volume, vol)
        self.volume = vol
        await self.run_setup_tasks()
        return self

    @property
    def stamp_path(self):
        if self.volume is None:
            raise RuntimeError('Volume not yet created')
        return self.volume.path

    @property
    def full_name(self):
        return self.config_layout.container_prefix+self.name
    async def run_container(self, *args, raise_on_running = True):
        async with self._operation_lock:
            if self.running:
                if raise_on_running:
                    raise RuntimeError('{} already running'.format(self))
                return self.process
            logger.info("Starting container {}: {}".format(
                self.name,
                " ".join(args)))
            self.process = sh.systemd_nspawn("--directory="+self.volume.path,
                                             '--machine='+self.full_name,
                                             *args,
                                             _bg = True,
                                             _bg_exc = False,
                                             _done = self._done_cb,
                                             _out = self._out_cb,
                                             _err_to_out = True,
                                             _tty_out = True,
                                             _encoding = 'utf-8',
                                             _new_session = False
                                             )
            
            self.running = True
            return self.process

    async def stop_container(self):
        async with self._operation_lock:
            if not self.running:
                raise RuntimeError("Container not running")
            self.process.terminate()
            self.process = None

    def _done_cb(self, cmd, success, code):
        logger.info("Container {} exited with code {}".format(
            self.name, code))
        self.running = False
        for f in self._done_waiters:
            if not f.canceled:
                f.set_result(code)
        self._done_waiters = []

    def _out_cb(self, data):
        logger.debug("Container {}: output {}".format(self. name,
                                                      data))

        
        for selector in self._out_selectors:
            r, cb, once = selector
            if cb is None: continue
            m = r.search(data)
            if m:
                try:
                    self.loop.call_soon_threadsafe(cb, m, data)
                except Exception:
                    logger.exception("Container {}: Error calling {}".format(
                        self.name, cb))
                if once:
                    # Free the RE and callback
                    selector[0:2] = [None, None]
                    
    def find_output(self, regexp, cb, once):
        regexp = re.compile(regexp)
        assert isinstance(once, bool)
        self._out_selectors.append([regexp, cb, once])

    async def start_container(self, *args):
        def callback(m, data):
            future.set_result(True)
        if self.running: return
        future = self.loop.create_future()
        self.find_output(r'\] Reached target Basic System', callback, True)
        await self.run_container("--boot", *args)
        await future
        logger.info("Container {} started".format(self.name))

    def close(self):
        if self.process is not None:
            try: self.process.terminate()
            except Exception: pass
            self.process = None
        if hasattr(self, volume):
            self.volume.close()
            del self.volume

    def __del__(self):
        self.close()
        
        
    @property
    def shell(self):
        if not self.running:
            raise RuntimeError("Container not running")
        return sh.machinectl.bake( "shell", self.full_name)


