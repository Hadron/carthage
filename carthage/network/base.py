from __future__ import annotations
import asyncio, dataclasses, logging, re, typing, weakref
from .. import sh
from ..dependency_injection import *
from ..config import ConfigLayout
from ..utils import permute_identifier, when_needed, memoproperty
from ..machine import ssh_origin, ssh_origin_vrf, Machine, AbstractMachineModel
from .config import V4Config

logger = logging.getLogger('carthage.network')

_cleanup_substitutions = [
    (re.compile(r'[-_\. ]'),''),
    (re.compile( r'database'), 'db'),
    (re.compile(r'test'),'t'),
    (re.compile(r'router'), 'rtr'),
    (re.compile(r'\..+'), ''),
]

_allocated_interfaces = set()

def if_name(type_prefix, layout, net, host = ""):
    "Produce 14 character interface names for networks and hosts"
    global _allocated_interfaces
    def cleanup(s, maxlen):
        for m, r in _cleanup_substitutions:
            s = m.sub(r, s)
        return s[0:maxlen]

    assert len(type_prefix) <= 3
    layout = cleanup(layout, 2)
    maxlen = 13-len(layout)-len(type_prefix)
    net = cleanup(net, max(3, maxlen-len(host)))
    maxlen -= len(net)
    host = cleanup(host, maxlen)
    if host: host += "-"
    id = "{t}{l}{h}{n}".format(
        t = type_prefix,
        l = layout,
        n = net,
        h = host)
    for i in permute_identifier(id, 14):
        if i not in _allocated_interfaces:
            _allocated_interfaces.add(i)
            return i
    assert False # never should be reached
    

class NetworkInterface:

    def __init__(self, network, ifname):
        self.ifname = ifname
        self.network = network

class VlanInterface(NetworkInterface):

    def __init__(self, id, network: BridgeNetwork):
        super().__init__(ifname = "{}.{}".format(
            network.bridge_name, id), network = network)
        self.vlan_id = id
        self.closed = False

    def close(self):
        if self.closed: return
        sh.ip("link", "del", self.ifname)
        self.closed = True

    def __del__(self):
        self.close()
        

        
class VethInterface(NetworkInterface):

    def __init__(self, network:BridgeNetwork, ifname, bridge_member_name):
        super().__init__(network, ifname)
        self.bridge_member_name = bridge_member_name
        self.closed = False

    def close(self):
        if self.closed: return
        try: sh.ip('link', 'del', self.bridge_member_name)
        except sh.ErrorReturnCode: pass
        del self.network.interfaces[self.bridge_member_name]
        self.closed = True

    def __del__(self):
        self.close()

class TechnologySpecificNetwork(AsyncInjectable):

    '''
    Abstract base class  for accessing a network.

    The :class:`.Network` class defines the interface to a virtual network.  However different backends require different ways of accessing a network.  For KVM we need a local bridge or macvlan interfaces.  Vmware needs some form of Portgroup on a VLAN.  This class is the abstract interface to that.

'''

    async def also_accessed_by(self, others: typing.List[TechnologySpecificNetwork]):
        '''
        Abstract method to notify a class of other technology specific networks.

        After construction, if any other technologies are in use, this method is called listing all of those technologies.  Later, if other technologies are added, this method is called again.

'''
        pass
    
                               


class Network(AsyncInjectable):

    '''
    Represents a network that VMs and containers can connect to.  In Carthage, networks are identified by a name and a VLAN identifier.  

    How networks are accessed depends on the underlying technology.  The base Network class maintains an `.Injector` so that only one instance of a technology-specific network is made for each logical network.

    .. seealso:

        BridgeNetwork
            For `carthage.Container` and `carthage.Vm`

        VmwareNetwork
            for `carthage.vmware.Vm`

    '''

    network_links: weakref.WeakSet


    def __init__(self, name, vlan_id = None, **kwargs):
        super().__init__(**kwargs)
        self.name = name
        self.vlan_id = vlan_id
        self.injector.add_provider(this_network, self)
        self.technology_networks = []
        self.network_links = weakref.WeakSet()

        

    async def access_by(self, cls: TechnologySpecificNetwork):
        '''Request a view of *self* using *cls* as a technology-specific lens.
        
        :return: The instance of *cls* for accessing this network
        '''
        assert issubclass(cls, TechnologySpecificNetwork), \
            "Must request access by a subclass of TechnologySpecificNetwork"
        instance = None
        if (cls not in self.ainjector) and self.vlan_id is not None:
            try:
                instance = await self.ainjector.get_instance_async(InjectionKey(cls, vlan_id = self.vlan_id))
                self.ainjector.add_provider(instance)
            except KeyError: pass
        if not instance: 
            instance = await self.ainjector.get_instance_async(cls)
        assert cls in self.ainjector, \
            f"It looks like {cls} was not registered with add_provider with allow_multiple set to True"
        if instance not in self.technology_networks:
            await instance.also_accessed_by(list(self.technology_networks))
            l = [instance]
            for elt in self.technology_networks:
                await elt.also_accessed_by(l)
            self.technology_networks.extend(l)
        return instance

    def close(self, canceled_futures = None):
        self.ainjector.close(canceled_futures = canceled_futures)
        self.technology_networks = []
        for l in list(self.network_links):
            try: l.close()
            except: logger.exception(f'Error closing link for {repr(self)}')
            

this_network = InjectionKey(Network, role = "this_network")

        
@inject(
    config_layout = ConfigLayout,
    injector = Injector,
    net = this_network)
class BridgeNetwork(TechnologySpecificNetwork):

    def __init__(self, net, *, bridge_name = None,
                 delete_bridge = True, **kwargs):
        super().__init__(**kwargs)
        self.name = net.name
        self.delete_bridge = delete_bridge
        self.interfaces = weakref.WeakValueDictionary()
        if bridge_name is None:
            self.bridge_name = if_name('br', self.config_layout.container_prefix, self.name)
        else: self.bridge_name = bridge_name
        self.closed = False
        self.members = []

    async def async_ready(self):
        try:
            sh.ip('link', 'show', self.bridge_name)
        except sh.ErrorReturnCode_1:
            sh.ip('link', 'add', self.bridge_name, 'type', 'bridge')
            sh.ip("link", "set", self.bridge_name, 
                    "type", "bridge", "stp_state", "1",
                    "forward_delay", "3")
            sh.ip("link", "set", self.bridge_name, "up")
        return await super().async_ready()

    def close(self):
        if self.closed: return
        self.members.clear()
        # Copy the list because we will mutate
        for i in list(self.interfaces.values()):
            try: i.close()
            except:
                logger.debug("Error deleting interface {}".format(i))
        if self.delete_bridge:
            logger.info("Network {} bringing down {}".format(self.name, self.bridge_name))
            sh.ip('link', 'del', self.bridge_name)
            self.closed = True

        
            
    def __del__(self):
        self.close()


    def add_member(self, interface):
        sh.ip("link", "set",
              interface.ifname,
              "master", self.bridge_name, "up")
        # We also keep a reference so that if it is a weak interface off another object it is not GC'd
        self.members.append(interface)
        
    def add_veth(self, container_name):
        bridge_member = if_name('ci', self.config_layout.container_prefix, self.name, container_name)
        veth_name = if_name('ve', self.config_layout.container_prefix, self.name, container_name)
        logger.debug('Network {} creating virtual ethernet for {}'.format(self.name, container_name))
        try:
            sh.ip('link', 'add', 'dev', bridge_member,
              'type', 'veth', 'peer', 'name', veth_name)
        except sh.ErrorReturnCode_2:
            logger.warn("Network {}: {} appears to exist; deleting".format(self.name, bridge_member))
            sh.ip('link', 'del', bridge_member)
            sh.ip('link', 'add', 'dev', bridge_member,
              'type', 'veth', 'peer', 'name', veth_name)
        sh.ip('link', 'set', bridge_member, 'master', self.bridge_name, 'up')
        ve = VethInterface(self, veth_name, bridge_member)
        self.interfaces[bridge_member] = ve
        return ve

    def expose_vlan(self, id):
        iface =  VlanInterface(id, self)
        ifname = iface.ifname
        try:
            sh.ip("link", "add",
                  "link", self.bridge_name,
                  "name", ifname,
                  "type", "vlan",
                  "id", id)
        except sh.ErrorReturnCode_2:
            logger.warn("{} appears to already exist".format(ifname))
        self.interfaces[ifname] = iface
        return iface

class NetworkConfig:

    '''Represents a network configuration for a :class:`~carthage.machine.Machine`.  A network config maps interface names to a network alink.  A network link contains a MAC address, a network, and other information.  Eventually a MAC is represented as a string and a
    network as a Network object.  However indirection is possible in
    two ways.  First, an injection key can be passed in; this
    dependency will be resolved in the context of an
    environment-specific injector.  Secondly, a callable can be passed
    in.  This callable will be called in the context of an injector
    and is expected to return the appropriate object.

    '''

    def __init__(self):
        self.link_specs = {}
        
    def add(self, interface, net, mac, **kwargs):
        assert isinstance(interface, str)
        kwargs['mac'] = mac
        kwargs['net'] = net
        NetworkLink.validate(kwargs, unresolved = True)
        self.link_specs[interface] = kwargs

    def __repr__(self):
        res = f'<{self.__class__.__name__}  {repr(self.link_specs)}>'
        return res
    

    @inject(ainjector = AsyncInjector)
    async def resolve(self, connection, ainjector) -> dict[str, NetworkLink]:
        '''
        Return a mapping of interfaces to NetworkLinks
        The *network_links* property of *connection* is updated based on the new network links.  That side effect is a bit unclean, but doing the update here allows :meth:`carthage.machine.Machine.resolve_networking` and :meth:`carthage.machine.AbstractMachineModel.resolve_networking` to have less duplicated code.
        '''
        async def resolve1(r:typing.any, i, args, k):
            if isinstance(r, InjectionKey):
                r = await ainjector.get_instance_async(r)
            elif  callable(r):
                r = await ainjector(r, i)
            args[k] = r
        def handle_other(link, other_args, other_future):
            def callback(future):
                other_link = None
                try:
                    try: other = future.result()
                    except Exception:
                        logger.exception(f'Error resolving other side of {link.interface} link of {link.machine}')
                        return
                    if not isinstance(other, (Machine, AbstractMachineModel)):
                        logger.error(f'The other side of the {interface} link to {link.machine} must be an Machine or AbstractMachineModel, not {other}')
                        return
                    if other_interface in other.network_links:
                        other_link = other.network_links[other_interface]
                        if link.net != other_link.net:
                            logger.error(f'Other side of {link.interface} link on {link.machine} connected to {other_link.net} not {link.net}')
                            return
                        if 'mac' in other_args and other_link.mac and \
                       other_link.mac != other_args['mac']:
                            logger.error(f'Other side of {link.interface} link on {link.machine} has MAC {other_link.mac} not {other_args["mac"]}')
                            return
                        for k,v in other_args.items(): setattr(other_link, k, v)
                    else: #other link exists
                        try:
                            other_args['net'] = link.net
                            other_link = NetworkLink(other, other_interface, other_args)
                        except Exception:
                            logger.exception(f'Error constructing {other_interface} link on {other} from {link.interface} link on {link.machine}')
                            return
                    link.other = other_link
                    other_link.other = link
                    other_future.set_result(other_link)
                finally:
                    if not other_future.done():
                        other_future.set_result(None)
                    
            other_interface = other_args.pop('other_interface')
            for k in list(other_args):
                k_new = k[6:]
                other_args[k_new] = other_args.pop(k)
            return callback
                    
        d = {}
        futures = []
        other_futures = []
        for i, link_spec in self.link_specs.items():
            link_args = {}
            for k,v in link_spec.items():
                if k == 'other' or k.startswith('other_'):
                    link_args.setdefault('_other', {})
                    if k != 'other' and (callable(v) or isinstance(v, InjectionKey)):
                        futures.append(asyncio.ensure_future(resolve1(v, i, link_args['_other'], k)))
                    else: link_args['_other'][k] = v
                    continue
                if callable(v) or isinstance(v, InjectionKey):
                    futures.append(asyncio.ensure_future(resolve1(v, i, link_args, k)))
                else: link_args[k] = v
            d[i] = link_args

        await asyncio.gather(*futures)
        del futures
        result: dict[str, NetworkLink] = {}
        for i, args in d.items():
            other_args = args.pop('_other', None)
            if other_args:
                if not 'other_interface' in other_args and 'other' in other_args:
                    raise RuntimeError(f'At least other_interface and other must be specified when specifying the other side of a link; {i} link on {connection}')
            try:
                link = NetworkLink(connection, i, args)
                result[i] = link
            except Exception:
                logger.exception( f"Error constructing {i} link on {connection}")
                raise
            if other_args:
                other_target = other_args.pop('other')
                #The future handling is complicated.  We want
                #something like
                #modeling.base.ModelingGroup.resolve_networking to be
                #able to wait for both sides of the link to come up.
                #But we also need a future to track when the target of
                #the other side has been resolved, because we cannot
                #construct the link before then.  So, we generate one
                #future for the other_target, and another future for
                #the fully constructed other link.
                other_future = ainjector.loop.create_future()
                other_futures.append(other_future)
                other_callback = handle_other(link, other_args, other_future)
                if isinstance(other_target, InjectionKey):
                    other_target_future = asyncio.ensure_future(ainjector.get_instance_async(other_target))
                elif callable(other_target):
                    other_target_future = asyncio.ensure_future(ainjector(other_target))
                else:
                    other_target_future = ainjector.loop.create_future()
                    other_target_future.set_result(other_target)
                # We don't wait on other_target_future because it's quite possible that the other side of the link will not resolve until after we resolve.
                other_target_future.add_done_callback(other_callback)
        for k, link in result.items():
            if k in connection.network_links:
                connection.network_links[k].close()
            connection.network_links[k] = link
        for l in connection.network_links.values():
            l.member_of = []
            try: del l.member_links
            except: pass
            try: del l.member_of_links
            except: pass
            l.member_links
            
        ainjector.emit_event(InjectionKey(NetworkConfig),
                             "resolved", self,
                             other_futures,
                             machine = connection,
                             adl_keys = self.supplementary_injection_keys(InjectionKey(NetworkConfig)))
        del other_futures
        
        return result

@dataclasses.dataclass
class NetworkLink:

    interface: str
    mac: typing.Optional[str]
    net: Network
    other: typing.Optional[NetworkLink]
    machine: object
    mtu: typing.Optional[int]
    local_type: typing.Optional[str]
    v4_config: typing.Optional[V4Config]

    def __new__(cls, connection, interface, args):
        if 'local_type' in args:
            cls = NetworkLink.local_type_registry[args['local_type']]
        return super().__new__(cls)
    

    def __init__(self,  connection, interface, args):
        self.validate(args)
        self.machine = connection
        self.interface = interface
        self.__dict__.update(args)
        self.net.network_links.add(self)
        for k in self.__class__.__annotations__:
            if not hasattr(self, k): setattr(self, k, None)
        self.member_of = []

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other
    

    async def instantiate(self, cls: typing.Type[TechnologySpecificNetwork]):
        if self.local_type is not None:
            self.net_instance = None
            return
        try: return self.net_instance
        except: pass
        self.net_instance =  await self.net.access_by(cls)
        return self.net_instance
        

    def __init_subclass__(cls, **kwargs):
        if hasattr(cls, 'local_type') and cls.local_type:
            NetworkLink.local_type_registry[cls.local_type] = cls
        super().__init_subclass__(**kwargs)

        
    local_type_registry: typing.ClassVar[typing.Mapping[str, NetworkLink]] = weakref.WeakValueDictionary()
    

    @classmethod
    def validate(cls, args: dict, unresolved:bool = False):
        try: subclass = NetworkLink.local_type_registry[args['local_type']]
        except KeyError: subclass = cls
        hints = typing.get_type_hints(subclass)
        if 'member' in args:
            args['members'] = [args['member']]
            del args['member']
        for k, t in hints.items():
            if k in ('machine', 'connection', 'interface', 'member_of'):
                if k in args:
                    raise TypeError( f'{k} cannot be specified directly')
                continue
            # don't know how to do this without accessing internals
            if t.__class__ == typing._UnionGenericAlias:
                t = typing.get_args(t)
                if type(None) in t: optional = True
                else: optional = False
            elif t.__class__ == typing._GenericAlias:
                continue
            else: #not a generic union alias
                optional = False
            if k not in args:
                if not optional:
                    raise TypeError(f'{k} is required')
            elif (not unresolved) and (not isinstance(args[k], t)):
                raise TypeError( f'{k} must be a {t} not {args[k]}')
        if subclass: subclass.validate_subclass(args, unresolved = unresolved)

    @classmethod
    def validate_subclass(cls, args, unresolved: bool): pass
    

    @memoproperty
    def member_links(self):
        res = []
        if not hasattr(self, 'members'): return res
        for l in self.members:
            try: res.append(self.machine.network_links[l])
            except KeyError:
                raise KeyError( f'{l} not found as an interface on {self.machine}') from None
        for link in res:
            if self.interface not in link.member_of:
                link.member_of.append(self.interface)
        return tuple(res)

    @memoproperty
    def member_of_links(self):
        res = []
        for l in self.member_of:
            try:
                res.append(self.machine.network_links[l])
            except KeyError:
                raise KeyError( f'{l} interface not found on {self.machine}') from None
        return tuple(res)


    def _merge(self, a):
        res = {}
        if hasattr(self.net, a):
            res.update(getattr(net, a))
        if hasattr(self, a):
            res.update(getattr(self, a))
        return res

    @memoproperty
    def merged_v4_config(self):
        if self.v4_config:
            return self.v4_config.merge(getattr(net, 'v4_config', None))
        return getattr(self.net, 'v4_config', V4Config())
    
            
    def close(self):
        if self.net:
            try: self.net.network_links.remove(self)
            except: pass
        if self.machine:
            try: del self.machine.network_links[self.interface]
            except: pass
        other = self.other
        self.other = None
        if other: other.close()
        try: self.net_instance.close()
        except: pass
        self.net_instance = None
        self.machine = None
        self.net = None
        
            


@inject(config_layout = ConfigLayout)
class NetworkConfigInstance(Injectable):

    def __init__(self, entries, config_layout):
        self.config_layout = config_layout
        self.entries = entries

    def __iter__(self):
        '''Return net, interface, MAC tuples.  Note that the caller is
        responsible for making the interface names line up correctly given the
        technology in question.
        '''

        for i,v in self.entries.items():
            yield v['net'], i, v['mac']

external_network_key = InjectionKey(Network, role = "external")

@inject(config_layout = ConfigLayout,
        injector = Injector)
class ExternalNetwork(Network):

    def __init__(self, config_layout, injector):
        vlan_id = config_layout.external_vlan_id
        kwargs = {}
        if vlan_id:
            kwargs['vlan_id'] = vlan_id
        super().__init__(name = "external network", injector = injector,
                         **kwargs)
        self.ainjector.add_provider(InjectionKey(BridgeNetwork),
                                   when_needed(BridgeNetwork, bridge_name = "brint", delete_bridge = False))

    @classmethod
    def supplementary_injection_keys(cls, k):
        yield external_network_key
        yield from super().supplementary_injection_keys(k)
        
external_network = when_needed(ExternalNetwork)

@dataclasses.dataclass
class HostMapEntry:

    ip: str
    mac: str = None

host_map_key = InjectionKey('host_map')


@inject(host_map = host_map_key,ainjector = AsyncInjector)
def mac_from_host_map(i, host_map, ainjector):
    from .machine import Machine
    machine = ainjector.get_instance(InjectionKey(Machine, _ready = False))
    entry = host_map[machine.name]
    machine.ip_address = entry.ip
    return entry.mac


@inject(ssh_origin = ssh_origin,
        ssh_origin_vrf = InjectionKey(ssh_origin_vrf, optional = True))
def access_ssh_origin( ssh_origin, ssh_origin_vrf, extra_nsenter = []):
    '''
        A container can be used as an ssh_origin, using the container as
        an injection point for entering a network under test.  This is
        typically done by constructing an ``nsenter`` command to enter the
        appropriate namespaces.  This function accomplishes that.

        :return: A list of arguments to be included in a *sh* call

        :param extra_nsenter: Extra namespaces to enter; something like ``['-p', '-m']``

'''
    vrf = []
    if ssh_origin_vrf:
        vrf = ['ip', 'vrf',
               'exec', ssh_origin_vrf]
    return sh.nsenter.bake( '-t', ssh_origin.container_leader,
                '-n',
                *vrf)
    


__all__ = r'''Network TechnologySpecificNetwork BridgeNetwork 
    external_network_key HostMapEntry mac_from_host_map host_map_key
access_ssh_origin
NetworkConfig NetworkLink
this_network
    '''.split()

from . import links as network_links

