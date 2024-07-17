# Copyright (C) 2019, 2020, 2021, 2022, 2023, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import asyncio
import abc
import copy
import dataclasses
import logging
import re
import typing
import weakref
from ipaddress import IPv4Address
from .. import sh
from ..dependency_injection import *
from ..config import ConfigLayout
from ..utils import permute_identifier, when_needed, memoproperty, is_optional_type, get_type_args
import carthage.kvstore
from ..machine import ssh_origin, ssh_origin_vrf, Machine, AbstractMachineModel
from .config import V4Config
ssh_origin_vrf_key = ssh_origin_vrf

logger = logging.getLogger('carthage.network')

_cleanup_substitutions = [
    (re.compile(r'[-_\. ]'), ''),
    (re.compile(r'database'), 'db'),
    (re.compile(r'test'), 't'),
    (re.compile(r'router'), 'rtr'),
    (re.compile(r'\..+'), ''),
]

_allocated_interfaces = set()


def if_name(type_prefix, layout, net, host=""):
    "Produce 14 character interface names for networks and hosts"
    global _allocated_interfaces

    def cleanup(s, maxlen):
        for m, r in _cleanup_substitutions:
            s = m.sub(r, s)
        return s[0:maxlen]

    assert len(type_prefix) <= 3
    layout = cleanup(layout, 2)
    maxlen = 13 - len(layout) - len(type_prefix)
    net = cleanup(net, max(3, maxlen - len(host)))
    maxlen -= len(net)
    host = cleanup(host, maxlen)
    if host:
        host += "-"
    id = "{t}{l}{h}{n}".format(
        t=type_prefix,
        l=layout,
        n=net,
        h=host)
    for i in permute_identifier(id, 14):
        if i not in _allocated_interfaces:
            _allocated_interfaces.add(i)
            return i
    assert False  # never should be reached


@dataclasses.dataclass
class NetworkInterface:

    network: object
    ifname: str
    delete_interface: bool = True
    closed: bool = dataclasses.field(init=False, default=False)

    def __del__(self):
        self.close()

    def close(self):
        if self.closed:
            return
        if self.delete_interface:
            self.delete_networking()
        self.closed = True

    def delete_networking():
        try:
            sh.ip("link", "del", self.ifname, _bg=False)
        except sh.ErrorReturnCode:
            pass
        self.closed = True


@dataclasses.dataclass
class VlanInterface(NetworkInterface):

    vlan_id: int = 0

    def __init__(self, id, network: BridgeNetwork, **kwargs):
        super().__init__(ifname="{}.{}".format(
            network.bridge_name, id), network=network, **kwargs)
        self.vlan_id = id
        self.closed = False


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

    How networks are accessed depends on the underlying technology.  The base Network class maintains an :class:`.Injector` so that only one instance of a technology-specific network is made for each logical network.

    .. seealso:

        BridgeNetwork
            For `carthage.Container` and `carthage.Vm`

        VmwareNetwork
            for `carthage.vmware.Vm`

    '''

    network_links: weakref.WeakSet

    def __init__(self, name, vlan_id=None, **kwargs):
        import carthage.kvstore
        super().__init__(**kwargs)
        self.name = name
        self.vlan_id = vlan_id
        self.injector.add_provider(this_network, self)
        self.injector.add_provider(V4Pool)
        self.technology_networks = []
        self.network_links = weakref.WeakSet()

    def add_network_link(self, link:NetworkLink):
        '''Indicate that  a network link has been added.  After this function teruns *link* in in *self.network_links*.
        '''
        self.network_links.add(link)
        self.injector.emit_event(
            InjectionKey(Network), "add_link",
            self, link=link,
            adl_keys={InjectionKey(Network, name=self.name)})

    def assign_addresses(self, link:NetworkLink=None):
        '''If links have addresses assigned from a :class:`carthage.kvstore.V4Pool`, assign those addresses.

        :param link: If non-None, only assign addresses for the given link.
        '''
        import carthage.kvstore
        pool = self.injector.get_instance(V4Pool)
        if link:
            return pool.assignment_loop([link])
        pool.new_assignments()
        pool.assignment_loop(self.network_links)
        
    async def access_by(self, cls: TechnologySpecificNetwork, ready=None):
        '''Request a view of *self* using *cls* as a technology-specific lens.

        :return: The instance of *cls* for accessing this network
        '''
        await self.async_become_ready()
        assert issubclass(cls, TechnologySpecificNetwork), \
            "Must request access by a subclass of TechnologySpecificNetwork"
        instance = None
        if (cls not in self.ainjector) and self.vlan_id is not None:
            try:
                instance = await self.ainjector.get_instance_async(InjectionKey(cls, vlan_id=self.vlan_id, _ready=ready))
                self.ainjector.add_provider(instance)
            except KeyError:
                pass
        if not instance:
            instance = await self.ainjector.get_instance_async(InjectionKey(cls, _ready=ready))
        assert cls in self.ainjector, \
            f"It looks like {cls} was not registered with add_provider with allow_multiple set to True"
        if instance not in self.technology_networks:
            await instance.also_accessed_by(list(self.technology_networks))
            l = [instance]
            for elt in self.technology_networks:
                await elt.also_accessed_by(l)
            self.technology_networks.extend(l)
        return instance

    def close(self, canceled_futures=None):
        self.ainjector.close(canceled_futures=canceled_futures)
        self.technology_networks = []
        for l in list(self.network_links):
            try:
                l.close()
            except BaseException:
                logger.exception(f'Error closing link for {repr(self)}')


    @property
    def v4_config(self):
        '''
        The :class:`V4Config` for this network.  When assigned to a *Network*, a *V4Config* cannot have any deferred elements.
        '''
        return self._v4_config

    @v4_config.setter
    def v4_config(self, config):
        assert isinstance(config, V4Config)
        config.after_resolve()
        self._v4_config = config

    def __init_subclass__(cls, **kwargs):
        if 'v4_config' in cls.__dict__:
            config = cls.v4_config
            del cls.v4_config
            config.after_resolve()
            cls._v4_config = config
        super().__init_subclass__(**kwargs)
            

this_network = InjectionKey(Network, role="this_network")


@inject(
    config_layout=ConfigLayout,
    injector=Injector,
    net=this_network)
class BridgeNetwork(TechnologySpecificNetwork):

    def __init__(self, net, *, bridge_name=None,
                 delete_bridge=None,
                 delete_interfaces=None, **kwargs):
        super().__init__(**kwargs)
        if delete_bridge is None:
            delete_bridge = not self.config_layout.persist_local_networking
        self.delete_bridge = delete_bridge
        self.name = net.name
        self.interfaces = weakref.WeakValueDictionary()
        if bridge_name is None:
            self.bridge_name = if_name('br', self.config_layout.container_prefix, self.name)
        else:
            self.bridge_name = bridge_name
        self.closed = False
        self.members = []
        if delete_interfaces is None and delete_bridge:
            delete_interfaces = True
        if delete_interfaces is None:
            delete_interfaces = not self.config_layout.persist_local_networking
        self.delete_interfaces = delete_interfaces

    async def async_ready(self):
        try:
            sh.ip('link', 'show', self.bridge_name, _bg=False)
        except sh.ErrorReturnCode_1:
            sh.ip('link', 'add', self.bridge_name, 'type', 'bridge', _bg=False)
            sh.ip("link", "set", self.bridge_name,
                  "type", "bridge", "stp_state", "1",
                  "forward_delay", "3", _bg=False)
            sh.ip("link", "set", self.bridge_name, "up", _bg=False)
        return await super().async_ready()

    def close(self):
        if self.closed:
            return
        self.members.clear()
        if self.delete_interfaces:
            self.delete_networking()
        self.closed = True
        self.interfaces.clear()

    def delete_networking(self):
        # Copy the list because we will mutate
        for i in list(self.interfaces.values()):
            try:
                i.close()
            except BaseException:
                logger.debug("Error deleting interface {}".format(i))
        if self.delete_bridge:
            logger.info("Network {} bringing down {}".format(self.name, self.bridge_name))
            sh.ip('link', 'del', self.bridge_name, _bg=False)
            self.closed = True

    def __del__(self):
        self.close()

    def add_member(self, interface):
        sh.ip("link", "set",
              interface.ifname,
              "master", self.bridge_name, "up", _bg=False)
        # We also keep a reference so that if it is a weak interface off another object it is not GC'd
        self.members.append(interface)

    def add_veth(self, link, namespace):
        bridge_member = if_name('ci', self.config_layout.container_prefix, self.name, link.machine.name)
        args = []
        if link.mtu:
            args.extend(['mtu', link.mtu])
        args.extend(['type', 'veth', 'peer'])
        if link.mac:
            args.extend(['address', str(link.mac)])
        args.extend(['name', link.interface])
        args.extend(['netns', namespace.name])
        logger.debug('Network {} creating virtual ethernet for {}'.format(self.name, link.machine.name))
        try:
            sh.ip('link', 'add', 'dev', bridge_member,
                  *args, _bg=False)
        except sh.ErrorReturnCode_2:
            logger.warn("Network {}: {} appears to exist; deleting".format(self.name, bridge_member))
            sh.ip('link', 'del', bridge_member, _bg=False)
            sh.ip('link', 'add', 'dev', bridge_member,
                  *args)
        sh.ip('link', 'set', bridge_member, 'master', self.bridge_name, 'up', _bg=False)
        ve = VethInterface(network=self, ifname=bridge_member, internal_name=link.interface, delete_interface=False)
        self.interfaces[bridge_member] = ve
        return ve

    def expose_vlan(self, id):
        iface = VlanInterface(id, self)
        ifname = iface.ifname
        try:
            sh.ip("link", "add",
                  "link", self.bridge_name,
                  "name", ifname,
                  "type", "vlan",
                  "id", id, _bg=False)
        except sh.ErrorReturnCode_2:
            logger.warn("{} appears to already exist".format(ifname))
        self.interfaces[ifname] = iface
        return iface


@dataclasses.dataclass
class VethInterface(NetworkInterface):

    internal_name: str = ""

    def close(self):
        if self.closed:
            return
        try:
            del self.network.interfaces[self.ifname]
        except KeyError:
            pass
        super().close()


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
        NetworkLink.validate(kwargs, unresolved=True)
        self.link_specs[interface] = kwargs

    def __repr__(self):
        res = f'<{self.__class__.__name__}  {repr(self.link_specs)}>'
        return res

    @inject(ainjector=AsyncInjector)
    async def resolve(self, connection, ainjector) -> dict[str, NetworkLink]:
        '''
        Return a mapping of interfaces to NetworkLinks
        The *network_links* property of *connection* is updated based on the new network links.  That side effect is a bit unclean, but doing the update here allows :meth:`carthage.machine.Machine.resolve_networking` and :meth:`carthage.machine.AbstractMachineModel.resolve_networking` to have less duplicated code.
        '''
        async def resolve1(r: typing.any, i, args, k):
            r = await resolve_deferred(ainjector, r, {"interface": i})
            if args is not None:
                args[k] = r
            return r

        def handle_other(link, other_args, other_future):
            def callback(future):
                other_link = None
                try:
                    try:
                        other = future.result()
                    except Exception:
                        logger.exception(f'Error resolving other side of {link.interface} link of {link.machine}')
                        return
                    if not isinstance(other, (Machine, AbstractMachineModel)):
                        logger.error(
                            f'The other side of the {other_interface} link to {link.machine} must be an Machine or AbstractMachineModel, not {other}')
                        return
                    if other_interface in other.network_links:
                        other_link = other.network_links[other_interface]
                        if link.net != other_link.net:
                            logger.error(
                                f'Other side of {link.interface} link on {link.machine} connected to {other_link.net} not {link.net}')
                            return
                        if 'mac' in other_args and other_link.mac and \
                                other_link.mac != other_args['mac']:
                            logger.error(
                                f'Other side of {link.interface} link on {link.machine} has MAC {other_link.mac} not {other_args["mac"]}')
                            return
                        for k, v in other_args.items():
                            setattr(other_link, k, v)
                    else:  # other link exists
                        try:
                            other_args['net'] = link.net
                            other_link = NetworkLink(other, other_interface, other_args)
                        except Exception:
                            logger.exception(
                                f'Error constructing {other_interface} link on {other} from {link.interface} link on {link.machine}')
                            return
                    link.other = other_link
                    other_link.other = link
                    other.network_links[other_link.interface] = other_link
                    other_link.member_of = []
                    other_future.set_result(other_link)
                finally:
                    if not other_future.done():
                        other_future.set_result(None)

            other_interface = other_args.pop('other_interface')
            if 'other_member_of' in other_args:
                raise ValueError('You cannot set member_of on the other side of a link.')
            if 'other_v4_config' in other_args:
                # It's possible to implement this.  We need to make sure after_resolve gets called all the time and resolve gets called on a new v4_config.  Since this function is not async, that gets messy.
                raise NotImplementedError('Currently you cannot set other_v4_config')
            for k in list(other_args):
                k_new = k[6:]
                other_args[k_new] = other_args.pop(k)
            return callback

        d = {}
        futures = []
        other_futures = []
        # For most of the machinery we want to define link memberships
        # in one direction: links have a members attribute that points
        # to their members and the rest is just memoized computation.
        # However, when specifying a configuration it is often
        # valuable (for example when specifying vlan links) to specify
        # things in the other direction.  So, we pull member_of off
        # the arguments and handle it later.
        members_of: dict[str, list] = {}
        for i, link_spec in self.link_specs.items():
            link_args = {}
            link_args_dict = link_spec.pop('link_args', {})
            if callable(link_args_dict) or isinstance(link_args_dict, InjectionKey):
                link_args_dict = await resolve1(link_args_dict, i, None, None)
                link_spec = dict(link_spec)
            link_spec.update(link_args_dict)
            del link_args_dict
            for k, v in link_spec.items():
                if k == 'other' or k.startswith('other_'):
                    link_args.setdefault('_other', {})
                    if k != 'other' and (callable(v) or isinstance(v, InjectionKey)):
                        futures.append(asyncio.ensure_future(resolve1(v, i, link_args['_other'], k)))
                    else:
                        link_args['_other'][k] = v
                    continue
                if k == 'member_of':
                    if callable(v) or isinstance(v, InjectionKey):
                        futures.append(asyncio.ensure_future(resolve1(v, i, members_of, i)))
                    else:
                        members_of[i] = v
                    continue
                if (
                    callable(v)
                    or isinstance(v, InjectionKey)
                    or isinstance(v, list)
                ):
                    futures.append(asyncio.ensure_future(resolve1(v, i, link_args, k)))
                else:
                    link_args[k] = v
            d[i] = link_args

        await asyncio.gather(*futures)
        del futures
        result: dict[str, NetworkLink] = {}
        for i, args in d.items():
            other_args = args.pop('_other', None)
            if other_args:
                if not 'other_interface' in other_args and 'other' in other_args:
                    raise RuntimeError(
                        f'At least other_interface and other must be specified when specifying the other side of a link; {i} link on {connection}')
            try:
                link = NetworkLink(connection, i, args)
                await link.resolve(ainjector=ainjector, interface=i)
                result[i] = link
            except Exception:
                logger.exception(f"Error constructing {i} link on {connection}")
                raise
            if other_args:
                other_target = other_args.pop('other')
                # The future handling is complicated.  We want
                # something like
                # modeling.base.ModelingGroup.resolve_networking to be
                # able to wait for both sides of the link to come up.
                # But we also need a future to track when the target of
                # the other side has been resolved, because we cannot
                # construct the link before then.  So, we generate one
                # future for the other_target, and another future for
                # the fully constructed other link.
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
                # We don't wait on other_target_future because it's quite possible that
                # the other side of the link will not resolve until after we resolve.
                other_target_future.add_done_callback(other_callback)
        # Now handle member_of and turn into members
        for i, member_of in members_of.items():
            for member in member_of:
                if member not in result:
                    raise ValueError(f'{i} has {member} in member_of, but {member} is not a link on {connection}')
                if not hasattr(result[member], 'members'):
                    result[member].members = []
                result[member].members.append(i)

        for k, link in result.items():
            if k in connection.network_links:
                connection.network_links[k].close()
            connection.network_links[k] = link
        for l in connection.network_links.values():
            l.member_of = []
            try:
                del l.member_links
            except BaseException:
                pass
            try:
                del l.member_of_links
            except BaseException:
                pass
        for l in connection.network_links.values():
            l.member_links

        ainjector.emit_event(InjectionKey(NetworkConfig),
                             "resolved", self,
                             pending_futures=other_futures,
                             machine=connection,
                             )
        del other_futures
        self._handle_deferred_macs(result)

        return result

    @staticmethod
    def _handle_deferred_macs(links: dict):
        from .mac import find_mac_first_member
        for l in links.values():
            if l.mac == 'inherit':
                l.mac = find_mac_first_member(l)


class VlanList(abc.ABC):

    '''Either an int, sequence of integers, a slice, or a sequence of slices
'''
    # This would be better represented as a typing.Union, but
    # unfortunately the NetworkLink.validate code cannot cope with that
    # without digging into internals of typing.

    @staticmethod
    def canonicalize(item: VlanList, link: NetworkLink):
        result = []
        if isinstance(item, (int, Network, CollectVlansType)):
            item = [item]
        for i in item:
            if isinstance(i, CollectVlansType):
                from .switch import link_collect_vlans
                result.extend(link_collect_vlans(link))
            elif isinstance(i, Network):
                if not i.vlan_id:
                    raise ValueError(f'{i} has no vlan_id set')
                result.append(i.vlan_id)
            elif isinstance(i, slice):
                result.append(i)
            elif isinstance(i, int):
                result.append(i)
            else:
                raise ValueError(f'{i} is not a valid VlanList member')
        return tuple(result)


VlanList.register(int)
VlanList.register(list)
VlanList.register(tuple)
VlanList.register(slice)


class CollectVlansType:
    pass


VlanList.register(CollectVlansType)
collect_vlans = CollectVlansType()


@dataclasses.dataclass
class NetworkLink:

    interface: str
    mac: typing.Optional[str]
    net: Network
    other: typing.Optional[NetworkLink]
    machine: object
    mtu: typing.Optional[int]
    local_type: typing.Optional[str]
    untagged_vlan: typing.Optional[int]
    allowed_vlans: typing.Optional[VlanList]
    v4_config: typing.Optional[V4Config] = dataclasses.field(default=None, repr=False)
    lldp: typing.Optional[bool] = dataclasses.field(default=True, repr=False)
    required: typing.Optional[bool] = dataclasses.field(default=True, repr=False)
    #: If true, this interface is essential and networkd should keep it up even if a dhcp lease expires or networkd is stopped
    precious: typing.Optional[bool] = dataclasses.field(default=False, repr=False)

    admin_status: typing.Optional[str] = dataclasses.field(default='up', repr=False)
    #: Sometimes it is desirable to have a different dns entry for an
    # interface than for the host as a whole. If set to a string, this
    # is the (potentially unqualified) dns name for the interface.  If
    # set to an empty string, the interface should not be registered in
    # dns.  Whether None implies the host should be registered under its name depends on how dns is configured.
    dns_name: typing.Optional[str] = None

    #: In NAT environments it is desirable to have a public DNS name separate than a private DNS name.  If set, then  when the public IP address is known, register this in dns.  It is more likely that this name needs to be fully qualified than dns_name.
    public_dns_name: typing.Optional[str] = None

    def __new__(cls, connection, interface, args):
        if 'local_type' in args:
            cls = NetworkLink.local_type_registry[args['local_type']]
        return super().__new__(cls)

    def __init__(self, connection, interface, args):
        self.validate(args)
        self.machine = connection
        self.interface = interface
        self.__dict__.update(args)
        self.net.add_network_link(self)
        for k in NetworkLink.__annotations__:
            if not hasattr(self, k):
                setattr(self, k, None)
        if self.mtu is None and getattr(self.net, 'mtu', None):
            self.mtu = self.net.mtu
        self.member_of = []
        if self.v4_config:
            self.v4_config = copy.copy(self.v4_config)

    def __hash__(self):
        return id(self)

    def __eq__(self, other):
        return self is other

    async def instantiate(self, cls: typing.Type[TechnologySpecificNetwork]):
        if self.local_type is not None:
            self.net_instance = None
            return
        try:
            return self.net_instance
        except BaseException:
            pass
        self.net_instance = await self.net.access_by(cls)
        return self.net_instance

    async def resolve(self, ainjector, interface):
        if 'merged_v4_config' in self.__dict__:
            raise ValueError('resolve called too late.')
        if self.v4_config is not None:
            await self.v4_config.resolve(ainjector=ainjector, interface=interface)
            
    def __init_subclass__(cls, **kwargs):
        if hasattr(cls, 'local_type') and cls.local_type:
            NetworkLink.local_type_registry[cls.local_type] = cls
        super().__init_subclass__(**kwargs)

    local_type_registry: typing.ClassVar[typing.Mapping[str, NetworkLink]] = weakref.WeakValueDictionary()

    @classmethod
    def validate(cls, args: dict, unresolved: bool = False):
        try:
            subclass = NetworkLink.local_type_registry[args['local_type']]
        except KeyError:
            subclass = cls
        hints = typing.get_type_hints(subclass)
        if 'member' in args:
            args['members'] = [args['member']]
            del args['member']
        for k, t in hints.items():
            if k in ('machine', 'connection', 'interface', 'member_of'):
                if k in args:
                    raise TypeError(f'{k} cannot be specified directly')
                continue
            optional = is_optional_type(t)
            if (not optional) and t.__class__ == typing._GenericAlias:
                continue
            if optional:
                t = get_type_args(t)
            if k not in args:
                if not optional:
                    raise TypeError(f'{k} is required')
            elif not unresolved:
                if hasattr(t, "__instancecheck__"):
                    if not t.__instancecheck__(args[k]):
                        raise TypeError(f'{k} must be a {t} not {args[k]}')
                else:
                    if not isinstance(args[k], t):
                        raise TypeError(f'{k} must be a {t} not {args[k]}')
        if subclass:
            subclass.validate_subclass(args, unresolved=unresolved)

    def __repr__(self):
        cls = self.__class__
        result = f'<{cls.__name__} '
        try:
            hints = typing.get_type_hints(cls)
            attributes = set(hints.keys())
            attributes -= {'local_type_registry', 'v4_config'}
            attributes |= {'merged_v4_config'}
            not_in_dict = set() #Attributes that are not in self.__dict__ before being queried.
            for k in attributes:
                if k not in self.__dict__: not_in_dict.add(k)
                if k == 'net':
                    result += f"net={self.net.name}"
                elif k == 'machine':
                    result += f'machine={self.machine.name}'
                else:
                    val = getattr(self, k, None)
                    if val is not None:
                        result += ' '+k+'='+repr(val)
        except Exception:
            result += 'repr failed'
        finally:
            for k in not_in_dict & set(self.__dict__.keys()):
                del self.__dict__[k]
        result += '>'
        return result
    
                
    @classmethod
    def validate_subclass(cls, args, unresolved: bool): pass

    @memoproperty
    def member_links(self):
        res = []
        if not hasattr(self, 'members'):
            return res
        for l in self.members:
            try:
                res.append(self.machine.network_links[l])
            except KeyError:
                raise KeyError(f'{l} not found as an interface on {self.machine}') from None
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
                raise KeyError(f'{l} interface not found on {self.machine}') from None
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
        '''Takes *self.v4_config* as a starting point, filling in any values specified on the network's *v4_config* that are not overridden in *self*.

        The merged config may be further modified by values discovered operationally.  As an example, if STUN or instance inspection discoveres the public address of some NAT connection, then that may be filled in by an implementation of :class:`~carthage.machine.Machine`.

        The merged configuration is guaranteed to be a (possibly shallow) copy of the link's *v4_config*.  That is changing a value directly on *self.merged_v4_config* will not change *self.v4_config*.
        

        '''
        if self.v4_config:
            merged = self.v4_config.merge(getattr(self.net, 'v4_config', None))
            if merged.address == merged.gateway:
                merged.gateway = None
            return merged
        if self.v4_config:
            return copy.copy(self.v4_config)
        elif hasattr(self.net, 'v4_config'):
            return copy.copy(self.net.v4_config)
        else: return V4Config()

    @memoproperty
    def private_to_public_map(self):
        result = {}
        def add(private, public):
            if private and public: result[private] = public
        config = self.merged_v4_config
        add(config.address, config.public_address)
        for a in config.secondary_addresses:
            add(a.private, a.public)
        return result
    

    def close(self):
        if self.net:
            try:
                self.net.network_links.remove(self)
            except BaseException:
                pass
        if self.machine:
            try:
                del self.machine.network_links[self.interface]
            except BaseException:
                pass
        other = self.other
        self.other = None
        if other:
            other.close()
        try:
            self.net_instance.close()
        except BaseException:
            pass
        self.net_instance = None
        self.machine = None
        self.net = None


@inject(config_layout=ConfigLayout)
class NetworkConfigInstance(Injectable):

    def __init__(self, entries, config_layout):
        self.config_layout = config_layout
        self.entries = entries

    def __iter__(self):
        '''Return net, interface, MAC tuples.  Note that the caller is
        responsible for making the interface names line up correctly given the
        technology in question.
        '''

        for i, v in self.entries.items():
            yield v['net'], i, v['mac']


external_network_key = InjectionKey(Network, role="external")


@inject(config_layout=ConfigLayout,
        injector=Injector)
class ExternalNetwork(Network):

    def __init__(self, config_layout, injector):
        vlan_id = config_layout.external_vlan_id
        external_bridge_name = config_layout.external_bridge_name
        kwargs = {}
        if vlan_id:
            kwargs['vlan_id'] = vlan_id
        super().__init__(name="external network", injector=injector,
                         **kwargs)
        self.ainjector.add_provider(InjectionKey(BridgeNetwork),
                                    when_needed(BridgeNetwork, bridge_name=external_bridge_name, delete_bridge=False))

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


@inject(host_map=host_map_key, ainjector=AsyncInjector)
def mac_from_host_map(i, host_map, ainjector):
    from .machine import Machine
    machine = ainjector.get_instance(InjectionKey(Machine, _ready=False))
    entry = host_map[machine.name]
    machine.ip_address = entry.ip
    return entry.mac


@inject(ssh_origin=ssh_origin,
        )
def access_ssh_origin(ssh_origin, ssh_origin_vrf=None, extra_nsenter=[]):
    '''
        A container can be used as an ssh_origin, using the container as
        an injection point for entering a network under test.  This is
        typically done by constructing an ``nsenter`` command to enter the
        appropriate namespaces.  This function accomplishes that.

        :return: A list of arguments to be included in a *sh* call

        :param extra_nsenter: Extra namespaces to enter; something like ``['-p', '-m']``

'''
    if ssh_origin_vrf is None:
        try:
            ssh_origin_vrf = ssh_origin.injector.get_instance(ssh_origin_vrf_key)
        except KeyError:
            pass
    vrf = []
    if ssh_origin_vrf:
        vrf = ['ip', 'vrf',
               'exec', ssh_origin_vrf]
    return sh.nsenter.bake('-t', ssh_origin.container_leader,
                           '-n',
                           *vrf)


def hash_network_links(network_links: dict[str, NetworkLink]):
    '''
    Return a hash value suitable for determining whether network_links have changed in setup_tasks.
'''
    def hash_subitem(i):
        result = 0
        if i is None:
            return 0
        if isinstance(i, int):
            return i
        for v in i:
            if isinstance(v, list):
                result += hash_subitem(v)
            elif isinstance(v, dict):
                result += hash_subitem(v.keys())
                result += hash_subitem(v.values())
            elif isinstance(v, str):
                for ch in v:
                    result += ord(ch)
            elif isinstance(v, int):
                result += v
        return result

    result = hash_subitem(network_links.keys())
    for v in network_links.values():
        result += hash_subitem(v.net.name)
        if v.mac:
            result += hash_subitem(v.mac)
        if v.machine:
            result += hash_subitem(v.machine.name)
        if v.mtu:
            result += v.mtu
        # Do not include public_v4_address because it tends to change regularly
        if v.allowed_vlans:
            result += hash_subitem(VlanList.canonicalize(v.allowed_vlans, v))
        if v.untagged_vlan:
            result += v.untagged_vlan
        if v.v4_config:
            result += hash_subitem(v.v4_config.__dict__.values())
        for attr in ('speed', 'portchannel_member', 'breakout_mode'):
            result += hash_subitem(getattr(v, attr, ''))
        try:
            result += hash_subitem(v.members)
        except AttributeError:
            pass
    return result


__all__ = r'''Network TechnologySpecificNetwork BridgeNetwork
    external_network_key HostMapEntry mac_from_host_map host_map_key
access_ssh_origin
NetworkConfig NetworkLink
VlanList collect_vlans
hash_network_links
this_network
    '''.split()
@inject_autokwargs(
    injector=Injector,
    network=this_network)
class V4Pool(carthage.kvstore.HashedRangeAssignments):

    def __init__(self, domain=None, **kwargs):
        network = kwargs['network']
        if domain is None:
            domain = network.name+'/v4_pool'
        super().__init__(domain=domain, **kwargs)
        self.network = network
        self.network.injector.add_event_listener(InjectionKey(carthage.Network), 'add_link', self._invalidate_caches_cb)
        # It is not guaranteed that all models will be instantiated
        # prior to address assignment, but in the common cases such as
        # CarthageLayout's async_ready calling layout level
        # resolve_networking, that will be the case.  The consequences
        # if we get this wrong are limited if prefer_reallocation is
        # False (the default).  We will prefer to use up all the
        # addresses before reusing them.  It would be possible to more
        # accurately set this setting, for example by looking up
        # CarthageLayout and triggering on resolve_networking or
        # similar.  Doing so would require a dependency on the
        # modeling layer (or some abstract base class to use) and a
        # way to do downward-directed events, which is not easy in
        # January of 2023.
        self.enable_key_validation()

    def close(self):
        try:
            self.network.remove_event_listener(InjectionKey(Network), self._invalidate_caches_cb)
        except BaseException: pass

    def _invalidate_caches_cb(self, *args, **kwargs):
        try: del self.valid_keys
        except AttributeError: pass

    @memoproperty
    def valid_keys(self):
        keys = set()
        for l in self.network.network_links:
            keys.add(self.link_key(l))
        return frozenset(keys)

    def find_bounds(self, link):
        v4_config = link.merged_v4_config
        if v4_config is None: return
        if v4_config.pool is None: return None
        return v4_config.pool

    def record_assignment(self, key, obj, assignment):
        if obj.v4_config is None:
            obj.v4_config = carthage.network.V4Config()
        obj.v4_config.address = IPv4Address(assignment)
        try: del obj.merged_v4_config
        except AttributeError: pass

    def link_key(self, link):
        return f'{link.machine.name}|{link.interface}'

    def assignment_loop(self, links):
        for link in links:
            bounds = self.find_bounds(link)
            if not bounds: continue
            key = self.link_key(link)
            if link.v4_config and link.v4_config.address:
                self.force_assignment(key, link, link.v4_config.address)
            else:
                self._assign(key, link)

    def str_to_assignment(self, assignment):
        return IPv4Address(assignment)
    
    def valid_key(self, k):
        return k in self.valid_keys
    
__all__ += ['V4Pool']

def match_link(links: dict[str,NetworkLink], 
               interface, *,
               mac=None, net:Network=None, address=None,
               excluded_links=None):
    '''Attempt to find the :class:`NetworkLink` corresponding to an interface on a VM.
    Ideally, links can be matched by name.  However, not all :class:`~carthage.machine.Machine` implementations will preserve link names in all situations.

    The following matches are tried in order:

    #. A link with interface of *interface* and compatible *net* and *mac*.  

    #. A link with mac address of *mac* and compatible *net*.

    #. A link with address of *address* and compatible *net* and *mac*.

    In the above, compatible means that either one property is None or the two properties are equal.

    :return: A matching link from *links* or None.

    :param excluded_links: A set of interface names that will not be
    matched.  It is an error if *interface* is in *excluded_links*.
    If a matching link is found and *excluded_links* is specified, it
    will be added to *excluded_links*.  That way, when this function
    is used in a a loop, links are matched at most once.
    '''
    def compatible(a,b):
        if a is None or b is None: return True
        return a == b
    if address: address = IPv4Address(address)
    if excluded_links is None: excluded_links = set()
    assert interface not in excluded_links
    match = None
    # try explicit name match
    try: match = links[interface]
    except KeyError: pass
    if match:
        if not (compatible(match.mac, mac) and compatible(match.net, net)):
            match = None
    # if we did not find a match,  enumerate if we have a mac address
    if mac is not None and match is None:
        for match in links.values():
            if match.interface in excluded_links: continue
            if match.mac != mac: continue
            if compatible(match.net, net): break
        else: match = None
    # Now try address matching
    if match is None and address is not None:
        for match in links.values():
            if match.interface in excluded_links: continue
            if match.merged_v4_config.address != address: continue
            if compatible(mac, match.mac) and compatible(net, match.net): break
        else: match = None
    if match:
        excluded_links.add(match.interface)
    logger.debug("Matching link %s, mac %s, net %s, address %s: %s",
                 interface, mac, net, address, match)
    return match

__all__ += ['match_link']


from . import links as network_links

