import weakref
from .dependency_injection import inject, Injectable
from .config import ConfigLayout
from .container import ssh_origin
from . import sh


@inject(config_layout = ConfigLayout)
class PortReservation:

    _ports_used = weakref.WeakSet()
    __slots__ = ('port', '__weakref__')

    def __init__(self, config_layout):
        assert config_layout.max_port > config_layout.min_port
        for i in range(config_layout.min_port, config_layout.max_port+1):
            if i not in  self._ports_used:
                self.port = i
                self._ports_used.add(self)
                break
            raise NoPortsError("No free ports")

    def __hash__(self):
        return hash(self.port)

    def __eq__(self, other):
        #Assumes that within the scope we care about for equality, a
        #given port reservation is unique.  If that's not true for a
        #subclass it's a good hint that containment rather than
        #subclassing is the right approach.
        if isinstance(other, int):
            return other == self.port
        elif type(other)  is type(self): 
            return self.port == other.port
        else: return super().__eq__(self)

        

@inject(config_layout = ConfigLayout,
        ssh_origin = ssh_origin)
class ExposedPort(PortReservation):

    __slots__ = ('expose_process',)

    bind_addr = '127.0.0.1'

    def __init__(self, dest_addr, *, config_layout, ssh_origin):
        super().__init__(config_layout = config_layout)
        self.expose_process = sh.socat(
            "tcp-listen:{},bind={bind},fork,reuseaddr".format(self.port, bind =self.bind_addr),
            'exec:"nsenter -t {leader} -n -i -m socat stdio {dest}",nofork'.format(
                leader = ssh_origin.container_leader,
                dest = dest_addr.replace('"', "\\\"")),
            _bg = True, _bg_exc = False)

    def close(self):
        if self.expose_process is not None:
            self.expose_process.terminate()
            self.expose_process = None


    def __del__(self):
        self.close()
        