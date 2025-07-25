# Copyright (C)  2022, 2023, 2024, 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from __future__ import annotations
import sys
import asyncio
import contextlib
import datetime
import json
import logging
from pathlib import Path
import tempfile
import shutil
import dateutil.parser
import carthage.machine
from carthage.dependency_injection import *
from .. import sh, ConfigLayout
from ..machine import AbstractMachineModel, Machine
from ..utils import memoproperty
from ..network import TechnologySpecificNetwork, Network, V4Config, this_network, NetworkConfig
from ..oci import *
from ..setup_tasks import setup_task, SetupTaskMixin, TaskWrapperBase, SkipSetupTask
from .container_host import instantiate_container_host,  construct_container_host, podman_container_host
import carthage.modeling


logger = logging.getLogger('carthage.podman')

def process_inspect_result(target:OciManaged, result:dict):
    match result:
        case {'Config':{'Labels': dict() as labels}} | \
             {'Labels': dict() as labels}:
            target.oci_labels.update(labels)

def podman_port_option(p: OciExposedPort):
    res = f'-p{p.host_ip}:{p.host_port}:{p.container_port}'
    if p.proto != 'tcp':
        res += f'/{p.proto}'
    return res


async def podman_mount_option(ainjector: AsyncInjector, m: OciMount):
    res = f'--mount=type={m.mount_type}'
    if m.source:
        source = await m.source_resolve(ainjector)
        assert source is not None,ValueError(f"'source' resolved to 'None' for {m}")
        if isinstance(source, PodmanVolume):
            await source.async_become_ready()
            source = source.name
        assert isinstance(source, str), 'Unrecognized type of source'
        res += f',source={source}'
    if m.destination:
        res += f',destination={m.destination}'
    else:
        raise TypeError('destination is required')
    if m.options:
        res += f',{m.options}'
    return res

__all__ = []

@inject(
    ainjector=AsyncInjector)
async def login_to_registry(tag:str, *, podman, ainjector):
    '''    Splits an image name on the first slash to find the name of the registry.
    Checks for ``InjectionKey(OciCredentials, registry=registry)`` in the supplied injector. If present that key can either be a :class:`OciCredentials` or a string in one of the following formats:

    * Password.  The username defaults to registry.  Note that gitlab registries do not appear to care what username you ese for a personal access token.

    * username:password

    '''
    registry, *rest = tag.partition('/')
    try:
        credentials = await ainjector.get_instance_async(InjectionKey(OciCredentials, registry=registry))
        match credentials:
            case OciCredentials(username=username, password=password):
                pass
            case str() as s if ':' in s:
                username, _, password = s.partition(':')
            case str() as s:
                username = 'registry'
                password = s
            case _:
                raise ValueError(f'Do not know how to interpret {credentials}')
        await podman('login', '-u', username, '-p', password, registry)
    except KeyError:
        pass
    
class HasContainerHostMixin(OciManaged):

    '''
    Provide dynamic_dependencies so podman objects are deleted after their host.
    '''

    async def dynamic_dependencies(self):
        if self.container_host is None:
            await self.ainjector(instantiate_container_host, self)
        result = await super().dynamic_dependencies()
        try:
            result += [self.container_host.machine]
        except AttributeError: pass
        return result


@inject_autokwargs(network=this_network)
class PodmanNetwork(HasContainerHostMixin, TechnologySpecificNetwork, OciManaged):

    container_host: PodmanContainerHost = None
    deployable_name_prefixes = ['podman_network:']

    @property
    def podman(self):
        return self.container_host.podman

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.name = self.network.name
        self.container_host = None

    def _gfi(self, key, default="error"):
        '''
        get_from_injector.  Used to look up some configuration in the model or its enclosing injectors.
        '''
        k = InjectionKey(key, _optional=default != "error")
        res = self.injector.get_instance(k)
        if res is None and default != "error":
            res = default
        return res

    async def find(self):
        if not self.container_host:
            await self.ainjector(instantiate_container_host, self)
        if not await self.container_host.find():
            logger.debug('%s does not exist because its container host does not exist', self)
            return False
        try:
            inspect_result = await self.podman(
                'network', 'inspect', self.network.name, _log=False)
        except Exception: return False
        info = json.loads(str(inspect_result))[0]
        try:
            return dateutil.parser.isoparse(info['created']).timestamp()
        except (KeyError, ValueError):
            logger.error('Unable to understand network inspection result: %s', info)
            raise NotImplementedError('Podman too old')


    async def do_create(self):
        options = ['-d', 'bridge']
        if bridge_name := self._gfi('podman_bridge_name', default=None):
            options.extend(['-ocom.docker.network.bridge.name='+bridge_name])
        podman_container_dns = self._gfi('podman_container_dns', default=True)
        if not podman_container_dns:
            options.extend(['--disable-dns'])
        podman_unmanaged = self._gfi('podman_unmanaged', default=None)
        #We handle unmanaged below because we must force unmanaged to True for dhcp
        podman_v4_config = self._gfi('podman_v4_config', default=None)
        # We handle merging with the network below
        from ..modeling import CarthageLayout
        layout = await self.ainjector.get_instance_async(InjectionKey(CarthageLayout, _optional=True))
        if layout_name := layout and layout.layout_name:
            options.extend(['--label', 'carthage.layout='+layout_name])
        v4_config = getattr(self.network, 'v4_config', None)
        if podman_v4_config and not v4_config:
            v4_config = podman_v4_config
            await v4_config.resolve(interface='podman', ainjector=self.ainjector)
        elif v4_config and podman_v4_config:
            v4_config = podman_v4_config.merge(v4_config)
            await v4_config.resolve(ainjector=self.ainjector, interface='podman')
        if v4_config:
            if v4_config.network:
                options.extend([
                    '--subnet', str(v4_config.network)])
            if v4_config.gateway is False:
                options.append('--internal')
            elif v4_config.gateway and v4_config.gateway  is not True:
                options.extend([
                    '--gateway', str(v4_config.gateway)])
            if v4_config.dhcp:
                options.extend(['--ipam-driver=dhcp'])
                podman_unmanaged = True
            # It is not clear how this interacts with container dns, but it seems better to specify dns servers on the podman-create line
#            for server in v4_config.dns_servers:
#                options.extend(['--dns', str(server)])
        if podman_unmanaged:
            options.append('-omode=unmanaged')
        await self.podman(
            'network',
            'create', self.network.name,
            *options)

    async def delete(self, force=True):
        if force:
            force_options = ['--force']
        else: force_options = []
        await self.podman(
            'network', 'rm', *force_options,
            self.network.name)

    def link_options(self, link):
        def safe(s):
            assert ',' not in s
            assert '=' not in s
            return s
        v4_config = link.merged_v4_config
        options = ['interface_name='+safe(link.interface)]
        if v4_config.address:
            options.append('ip='+safe(str(v4_config.address)))
        if link.mac:
            options.append('mac='+safe(link.mac))
        if link.mtu:
            options.append('mtu='+safe(link.mtu))
        assert ':' not in self.network.name
        return safe(self.network.name)+':'+','.join(options)

__all__ += ['PodmanNetwork']

class PodmanNetworkMixin:
    network_implementation_class = PodmanNetwork

    async def _setup_networks(self):
        for l in self.network_links.values():
            if l.local_type: continue
            await l.instantiate(PodmanNetwork)

    async def _network_options(self):
        await self._setup_networks()
        options = []
        for l in self.network_links.values():
            if l.local_type: continue
            l.net.assign_addresses(l)
            options.extend(['--network', l.net_instance.link_options(l)])
        return options

    async def _container_network_options(self):
        # Like network_options but handles network_namespace_key
        # Resolve networking to chase down network_namespace so the
        # pod membership test works.
        await self.resolve_networking()
        try:
            network_namespace = await self.ainjector.get_instance_async(carthage.machine.network_namespace_key)
        except KeyError: network_namespace = False
        if self.pod and self.network_links is not self.pod.network_links:
            logger.error(f"{self.name} will not join network of {self.pod}; if this is intended then add_provider(network_namespace_key, dependency_quote(None) in {self.name}'s model.  If it is not, then set network_namespace_key to {self.pod}")
        if network_namespace:
            if isinstance(network_namespace, PodmanContainer):
                await network_namespace.find_or_create()
                return ['--network', 'container:'+network_namespace.id]
            elif self.pod:
                return []           # Joining a pod
        return await self._network_options() # Creating our own namespace

    async def resolve_networking(self, force:bool = False):
        '''Like
        :meth:`carthage.machine.NetworkedModel.resolve_networking`
        except that it looks for :data:`oci_container_network_config`.
        If that key is present, that network config is used instead
        of ``InjectionKey(NetworkConfig)``.  Doing so allows
        containers that are lexically contained in their host to have
        their own NetworkConfig.

        '''
        if not force and self.network_links:
            return
        container_config = await self.ainjector.get_instance_async(InjectionKey(oci_container_network_config, _optional=NotPresent))
        if container_config is not NotPresent:
            try:
                self.injector.add_provider(InjectionKey(NetworkConfig), dependency_quote(container_config))
            except ExistingProvider: pass
        await super().resolve_networking(force=force)
        for l in self.network_links.values():
            if l.local_type: continue
            l.net.assign_addresses(l)

@inject(
    podman_pod_options=InjectionKey('podman_pod_options', _optional=NotPresent),
)
class PodmanPod(HasContainerHostMixin, PodmanNetworkMixin, carthage.machine.NetworkedMixin, OciPod):

    #: A list of extra options to pass to pod create
    podman_pod_options = []

    deployable_name_prefixes = ['podman_pod', 'pod']

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.injector.add_provider(InjectionKey(PodmanPod), dependency_quote(self))
        self.network_links = {}
        self.container_host = None

    async def find(self):
        if not self.container_host:
            await self.ainjector(instantiate_container_host, self)
        if not await self.container_host.find():
            logger.debug('%s does not exist because its container host does not exist', self)
            return False
        if self.id:
            inspect_arg = self.id
        else:
            inspect_arg = self.name
        try:
            result = await self.podman(
                'pod', 'inspect', inspect_arg, _log=False)
        except sh.ErrorReturnCode:
            return False
        pod_info = json.loads(str(result.stdout, 'utf-8'))
        if isinstance(pod_info, list):
            pod_info = pod_info[0]
        self.pod_info = pod_info
        return dateutil.parser.isoparse(pod_info['Created']).timestamp()

    async def do_create(self):
        await self.resolve_networking()
        options = await self._network_options()
        for p in self.exposed_ports:
            options.append(podman_port_option(p))
        options.extend(self.podman_pod_options)
        from ..modeling import CarthageLayout
        layout = await self.ainjector.get_instance_async(InjectionKey(CarthageLayout, _optional=True))
        if layout_name := layout and layout.layout_name:
            options.extend(['--label', 'carthage.layout='+layout_name])
        await self.podman(
            'pod', 'create',
            *options,
            '--name=' + self.name)

    async def delete(self, force=False):
        force_options = []
        if force:
            force_options.append('--force')
        await self.podman(
            'pod', 'rm',
            *force_options,
            self.name)

    @memoproperty
    def podman(self):
        return self.container_host.podman


__all__ += ['PodmanPod']



@inject_autokwargs(
    oci_container_image=InjectionKey(oci_container_image, _optional=NotPresent),
    podman_restart=InjectionKey('podman_restart', _optional=NotPresent),
    pod=InjectionKey(PodmanPod, _optional=True),
    podman_options=InjectionKey('podman_options', _optional=NotPresent),
)
class PodmanContainer(HasContainerHostMixin, PodmanNetworkMixin, Machine, OciContainer):

    '''
An OCI container implemented using ``podman``.  While it is possible to set up a container to be accessible via ssh and to meet all the interfaces of :class:`~carthage.machine.SshMixin`, this is relatively uncommon.  Such containers often have an entry point that is not a full init, and only run one service or program.  Typically :meth:`container_exec` is used to execute an additional command in the scope of a container rather than using :meth:`ssh`.
    '''

    #: Timeout in seconds to wait when stopping a container
    stop_timeout = 10
    machine_running_ssh_online = False
    rsync_uses_filesystem_access = True

    #: restart containers (no, always, on-failure)
    podman_restart = 'no'

    #:Extra options (as a list) to be passed into podman create
    @memoproperty
    def podman_options(self):
        '''Extra options to be passed into podman create as a list
        '''
        try:
            return self.model.podman_options
        except AttributeError:
            return []


    @memoproperty
    def ssh_options(self):
        if not hasattr(self, 'ssh_port'):
            raise ValueError('Set ssh_port before ssh')
        return (
            *super().ssh_options,
            f'-p{self.ssh_port}')

    #:The port on which to connect to for ssh
    ssh_port: int

    @memoproperty
    def ansible_inventory_overrides(self):
        return dict(
            ansible_connection='containers.podman.podman',
            ansible_podman_extra_args=self.container_host.extra_args,
            ansible_pipelining=False,
            ansible_host=self.full_name,
        )

    deployable_name_prefixes = ['container', 'podman', 'machine']

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._operation_lock = asyncio.Lock()
        self.container_host = None

    @memoproperty
    def podman(self):
        return self.container_host.podman


    async def find(self):
        if not self.container_host:
            await self.ainjector(instantiate_container_host, self)
        if not await self.container_host.find():
            logger.debug('%s does not exist because its container host does not exist', self)
            return False
        await self.resolve_networking()
        try:
            result = await self.podman(
                'container', 'inspect', self.full_name,
                _bg=True, _bg_exc=False, _log=False)
        except sh.ErrorReturnCode:
            return False
        containers = json.loads(str(result))
        process_inspect_result(self, containers[0])
        self.container_info = containers[0]
        ports = self.container_info['NetworkSettings']['Ports']
        if not hasattr(self, 'ssh_port') and '22/tcp' in ports:
            if ports['22/tcp']:
                self.ssh_port = ports['22/tcp'][0]['HostPort']
        self.id = self.container_info['Id']
        self.running = self.container_info['State']['Running']
        try:
            return dateutil.parser.isoparse(containers[0]['Created']).timestamp()
        except Exception as e:
            raise ValueError(f'Invalid ISO string: {self.container_info["Created"]}')


    async def do_create(self):
        image = self.oci_container_image
        if isinstance(image, OciImage):
            await image.async_become_ready()
            image = image.oci_image_tag
        if self.pod:
            await self.pod.async_become_ready()
        command_options = []
        if self.oci_command:
            command_options = list(self.oci_command)
        network_options = await self._container_network_options()
        await self.podman(
            'container', 'create',
            f'--name={self.full_name}',
            *(await self._podman_create_options()),
            *network_options,
            image,
            *command_options,
            _bg=True, _bg_exc=False)

    async def _podman_create_options(self):
        options = []
        from ..modeling import CarthageLayout
        layout = await self.ainjector.get_instance_async(InjectionKey(CarthageLayout, _optional=True))
        if layout_name := layout and layout.layout_name:
            options.extend(['--label', 'carthage.layout='+layout_name])
        options.append('--restart=' + self.podman_restart)
        if self.oci_interactive:
            options.append('-i')
        if self.oci_tty:
            options.append('-t')
        for k, v in self.injector.filter_instantiate(
                OciEnviron, lambda k: 'name' in k.constraints and k.constraints.get('scope', 'all') in ('all', 'container')):
            options.append('-e' + v.assignment)
        if not self.pod:
            for p in self.exposed_ports:
                options.append(podman_port_option(p))
        else:  # there is a pod
            options.append('--pod=' + self.pod.name)
        for m in self.mounts:
            options.append(await podman_mount_option(self.ainjector, m))
        options.extend(self.podman_options)
        return options

    async def delete(self, force=True, volumes=True):
        if not self.container_host:
            await self.ainjector(instantiate_container_host, self)
        force_args = []
        if force:
            force_args.append('--force')
        if volumes:
            force_args.append('--volumes')
        await self.podman(
            'container', 'rm',
            *force_args, self.full_name,
            _bg=True, _bg_exc=False)

    async def is_machine_running(self):
        if self.container_host is None:
            await self.ainjector(instantiate_container_host, self)
        if not await self.container_host.start_container_host(False):
            # When the container host is not running, the container is not running
            self.running = False
            return False
        if not await self.find():
            return False # Containers that do not exist are not running
        self.running = self.container_info['State']['Running']
        return self.running

    async def start_machine(self):
        if self.readonly:
            self.logger_for().info('Not starting %s which is readonly', self)
            return
        async with self._operation_lock:
            await self.is_machine_running()
            if self.running:
                return
            await self.start_dependencies()
            await super().start_machine()
            logger.info(f'Starting {self.full_name}')
            await self.podman(
                'container', 'start', self.full_name,
                _bg=True, _bg_exc=False)
        await self.is_machine_running()

    async def stop_machine(self):
        async with self._operation_lock:
            await self.is_machine_running()
            if not self.running:
                return
            logger.info(f'Stopping {self.full_name}')
            try:
                await self.podman(
                    'container', 'stop',
                    f'-t{self.stop_timeout}',
                    self.full_name,
                    _bg=True, _bg_exc=False)
            except sh.ErrorReturnCode_125 as e:
                if b'rootless netns' in e.stderr and b'kill network process' in e.stderr:
                    # https://bugs.debian.org/1100135
                    pass
                else:
                    raise
            self.running = False
            await super().stop_machine()

    def container_exec(self, *args, _user=None, _fg=False):
        '''
Execute a command in a running container and return stdout.  This function intentionally has a differentname than :meth:`carthage.container.Container.container_command` because that method does not expect the container to be running.
'''
        if not self.running:
            raise RuntimeError(f'{self.name} is not running')
        if _user is None:
            _user = self.runas_user
        if _user != 'root':
            raise NotImplementedError('only can run as root for now')
        if _fg:
            interactive = ['-ti']
        else:
            interactive = []
        result = self.podman(
            'container', 'exec',
            *interactive,
            self.full_name,
            *args,
            _log=False, _fg=_fg,
            )
        return result

    #: An alias to be more compatible with :class:`carthage.container.Container`
    shell = container_exec
    #: container_exec meets the run_command interface
    run_command = container_exec

    def _apply_to_filesystem_customization(self, customization):
        @contextlib.asynccontextmanager
        async def customization_context():
            async with self.machine_running(ssh_online=False), self.filesystem_access() as path:
                customization.path = path
                yield
            return
        customization.customization_context = customization_context()

    def filesystem_access(self, user='root'):
        assert self.container_host, 'call self.find first'
        return self.container_host.filesystem_access_container(self.full_name)

    def __repr__(self):
        try:
            host = repr(self.container_host)
        except Exception:
            host = "repr failed"
        return f'<{self.__class__.__name__} {self.name} on {host}>'

    @memoproperty
    def stamp_subdir(self):
        return 'podman/'+self.name

    def check_stamp(self, stamp, raise_on_error=False):
        mtime, text = super().check_stamp(stamp, raise_on_error)
        creation = getattr(self, '_find_result', None)
        if creation and mtime < creation:
            return False, "" #stamp predates container creation
        return mtime, text

__all__ += ['PodmanContainer']


class PodmanImageBuilderContainer(PodmanContainer):

    oci_command = ['sleep', '3600']
    stop_timeout = 1

    exposed_ports = [] #: Image builders should not generally expose ports.
    def _apply_to_container_customization(self, customization):
        @contextlib.asynccontextmanager
        async def customization_context():
            async with self.machine_running(ssh_online=False), self.filesystem_access() as path:
                customization.path = path
                customization.container_command = self.container_exec
                customization.running = False # It is technically running, but not with any services
                yield
            return
        customization.customization_context = customization_context()

    @memoproperty
    def model(self):
        res =  self.injector.get_instance(InjectionKey(AbstractMachineModel, _ready=False, _optional=True))
        if res is None: raise AttributeError
        return res

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.injector.add_provider(InjectionKey(NetworkConfig), dependency_quote(None))
        self.injector.replace_provider(carthage.machine.network_namespace_key, dependency_quote(None))

#:If True, push nonlocal images to their registry.  Typically set on
#specific images or in a group of images that the current layout is
#responsible for.
podman_push_images = InjectionKey('carthage.podman.push_images', _optional=True)

__all__ += ['podman_push_images']

def image_is_local(tag:str|None):
    '''None is local because it typically means image by id not tag.
    Otherwise images starting with localhost are local.
    '''
    if tag is None: return True
    if tag.startswith('localhost/'):
        return True
    return False

@inject_autokwargs(
    base_image=InjectionKey(oci_container_image, _optional=NotPresent, _ready=False),
)
class PodmanImage(OciImage, SetupTaskMixin, no_auto_inject=True):

    '''
    Represents an OCI container image and provides facilities for building the image.

    :class:`customizations <carthage.machine.BaseCustomization>` can be turned into image layers using the :func:`image_layer_customization` function.  Note that :func:`setup_tasks <setup_task>` are only run when images are actually built.  By default, the image is only built if it does not exist, although see :meth:`should_build` to override.

    '''

    last_layer = None
    base_image = None

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.layer_number = 1
        self.injector.add_provider(InjectionKey('podman_log'), self.log_path/'podman.log')
        self.container_host = None

    async def pull_base_image(self):
        if not self.container_host:
            await self.ainjector(instantiate_container_host, self)
        if isinstance(self.base_image, OciImage):
            await self.base_image.async_become_ready()
            base_image = self.base_image.id
        else:
            image = await self.ainjector(PodmanImage, oci_image_tag=self.base_image, base_image=None, readonly=True)
            await image.async_become_ready()
            if not image.id:
                raise LookupError(f'Failed to find {self.base_image}')
            base_image = image.id
        inspect_result = await self.podman(
            'image', 'inspect',
            base_image, _log=False)
        image_info = json.loads(str(inspect_result))[0]
        self.parse_base_image_info(image_info)

    def parse_base_image_info(self, image_info):
        config = image_info['Config']
        if not self.oci_image_command and 'Cmd' in config:
            self.oci_image_command = config['Cmd']
        if not self.oci_image_entry_point and 'Entrypoint' in config:
            self.oci_image_entry_point = config['Entrypoint']
        self.base_image_info = image_info
        self.last_layer = self.base_image_info['Id']

    async def build_or_pull(self):
        pull_policy = self.config_layout.podman.pull_policy
        will_build = False
        if image_is_local(self.oci_image_tag):
            will_build = True
            pull_policy = 'never'
        else:
            # For now we only are willing to build non-local images if we would push them.
            will_build = await self.ainjector.get_instance_async(podman_push_images)
        if self.readonly:
                will_build = False
        exists = self.id or bool(await self.find())
        if will_build and exists:
            should_build =  await self.should_build()
            if should_build:
                return 'build'
            else:
                return '' #Use the existing image.
        elif will_build:
            return 'build'
        elif exists:
            if pull_policy == 'missing':
                return ''
            else:
                return 'pull'
        else: #not buildable, not exists
            if image_is_local(self.oci_image_tag):
                raise RuntimeError(f'{self} is a local image that cannot be built.')
            return 'pull'
        raise AssertionError('Logic error: should be unreachable')

    async def pull_image(self):
        pull_policy = self.config_layout.podman.pull_policy
        if pull_policy == 'never':
            return
        if not image_is_local(self.oci_image_tag):
            await self.ainjector(login_to_registry, self.oci_image_tag, podman=self.podman)
        try:
            self.id = None
            await self.podman('pull', self.oci_image_tag)
            await self.find()
        except sh.ErrorReturnCode:
            if pull_policy == 'newer' and await self.find():
                return
            raise

    async def push_image(self):
        assert self.id
        assert not image_is_local(self.oci_image_tag)
        logger.info('Pushing %s', self)
        await self.podman('push', self.oci_image_tag)

    async def find(self):
        if not self.container_host:
            await self.ainjector(instantiate_container_host, self)
        if not await self.container_host.find():
            logger.debug('%s does not exist because its container host does not exist', self)
            return False
        if self.id:
            to_find = self.id
        else:
            to_find = self.oci_image_tag
        try:
            result = await self.podman(
                'image', 'inspect', to_find,
                _log=False)
        except sh.ErrorReturnCode:
            return False
        info = json.loads(str(result))[0]
        self.id = info['Id']
        process_inspect_result(self, info)
        self.image_info = info
        return dateutil.parser.isoparse(info['Created']).timestamp()

    @contextlib.asynccontextmanager
    async def filesystem_access(self):
        await self.find()
        async with self.container_host.filesystem_access_image(self.oci_image_tag) as path:
            yield path

    async def should_build(self):
        '''If the image exists, this is called.  If it returns True, then the image will be rebuilt even though it exists.  If a caller wants to force a rebuild, :meth:`build_image` can also be called.

        Local images that exist are not rebuilt by default.  Remote
        images are rebuilt under the assumption that if building is
        enabled at all, the goal is to build and push a new image.

        Images may overide if they have a good way to tell if an image is out of date.
        '''
        return not image_is_local(self.oci_image_tag)

    async def delete(self):
        '''Try deleting an image.  If we get an error (probably
        because it is in use by a container), untag instead.

        '''
        try:
            await self.podman('image', 'rm', self.oci_image_tag)
        except sh.ErrorReturnCode:
            await self.podman('image', 'untag', self.oci_image_tag)

    @contextlib.asynccontextmanager
    async def image_layer_context(self, commit_message=""):
        '''
        Generate a container to produce  a new image layer:

        * The image of the container will be either *self.last_layer* or *self.base_image* if *last_layer* is not set.

        * The container will be a :class:`PodmanImageBuilderContainer`, and as such will simply pause when started so that :meth:`container_exec` can be used to run commands in the container.

        Usage::

            async with self.image_layer_context() as layer_container:
                # Apply customizations/run commands in layer_container
            #Now, self.last_layer is the image ID of the new layer
        '''
        def container_delete(future):
            try:
                future.result()
            except Exception as e:
                logger.error('Error deleting %s: %s', layer_container, str(e))

        base_image = self.last_layer or self.base_image
        layer_container = await self.ainjector(
            PodmanImageBuilderContainer,
            oci_container_image=base_image,
            name=f'carthage-image-build-{id(self)}-l{self.layer_number}',
        )
        self.layer_number += 1
        try:
            await layer_container.start_machine()
            yield layer_container
            await self.commit_container(layer_container, commit_message)
        finally:
            delete_task = asyncio.get_event_loop().create_task(layer_container.delete())
            delete_task.add_done_callback(container_delete)

    def _commit_options(self):
        entrypoint = None
        cmd = None
        if self.oci_image_entry_point:
            entrypoint = json.dumps(self.oci_image_entry_point)
        if self.oci_image_command:
            cmd = json.dumps(self.oci_image_command)
        options = []
        if cmd:
            options.append('--change=CMD ' + cmd)
        if entrypoint:
            options.append('--change=ENTRYPOINT ' + entrypoint)
        for k, v in self.injector.filter_instantiate(
                OciEnviron, lambda k: 'name' in k.constraints and k.constraints.get('scope', 'all') in ('all','image')):
            options.append('--change=ENV '+v.assignment)
        return options

    async def commit_container(self, container, commit_message):
        options = self._commit_options()
        from ..modeling import CarthageLayout
        layout = await self.ainjector.get_instance_async(InjectionKey(CarthageLayout, _optional=True))
        if layout_name := layout and layout.layout_name:
            options.extend(['--change', 'LABEL carthage.layout='+layout_name])
        if self.oci_image_author:
            options.append('--author=' + self.oci_image_author)
        if commit_message:
            options.append('-fdocker')
            options.append('--message=' + commit_message)
        # options must be quoted if it's going through ssh or something that can split args on space
        # We use podman_nosocket because we have run into trouble with trixie podman driving a bookworm container host.
        commit_result = await self.container_host.podman_nosocket(
            'container', 'commit',
            *options,
            container.id, _log=False)
        self.last_layer = str(commit_result.stdout, 'utf-8').strip()

    async def tag_last_layer(self):
        assert self.last_layer
        await self.podman(
            'image', 'tag',
            self.last_layer, self.oci_image_tag)
        self.id = self.last_layer
        await self.find()

    async def find_or_create(self):
        '''See if image exists otherwise rebuild the image.
        Note that this is not a :func:`setup_task` even though it is in the parent.  This is always run from :meth:`async_ready`
        '''
        await self.find()
        match await self.build_or_pull():
            case 'build':
                await self.build_image()
            case 'pull':
                await self.pull_image()
        # There is fallthrough if we should neither build nor pull (keep existing)
        if not self.id:
            raise LookupError('Unable to find or create image')

    async def build_image(self):
        assert not self.readonly, 'Cannot build a readonly image'
        await self.pull_base_image()
        # You might think that context for run_setup_tasks should be
        # self.image_layer_context().  If it worked that way, then
        # everything would end up in a single layer.  Instead, use
        # image_layer_task for wrapping customizations and explicitly
        # call image_layer_context in setup_tasks.
        await self.run_setup_tasks()
        if not self.last_layer:
            logger.warn('%s failed to generate any image layers', self)
            return
        await self.tag_last_layer()

    async def async_ready(self):
        await self.find_or_create()
        return await AsyncInjectable.async_ready(self)

    async def deploy(self):
        '''Bring image to ready, and if nonlocal and pushable, push.
        '''
        await self.async_become_ready()
        push = await self.ainjector.get_instance_async(podman_push_images)
        if push and not image_is_local(self.oci_image_tag):
            if self.readonly:
                logger.info('Not pushing readonly image %s', self)
                return
            await self.push_image()

    @memoproperty
    def podman(self):
        return self.container_host.podman


    @memoproperty
    def stamp_subdir(self):
        return 'podman_image/'+self.oci_image_tag

    @classmethod
    def supplementary_injection_keys(cls, k):
        yield InjectionKey(PodmanImage, oci_image_tag=cls.oci_image_tag)
        yield from super().supplementary_injection_keys(k)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if tag := getattr(cls, 'oci_image_tag', None):
            carthage.modeling.propagate_key(
                InjectionKey(PodmanImage, oci_image_tag=tag, _globally_unique=True),
                cls)


__all__ += ['PodmanImage']
podman_image_volume_key = InjectionKey('carthage.podman/image_volume')


@inject(base_image=None)
@inject_autokwargs(
    image_volume=podman_image_volume_key,
)
class PodmanFromScratchImage(PodmanImage):

    async def pull_base_image(self):
        await self.image_volume.async_become_ready()
        async with self.container_host.tar_volume_context(self.image_volume) as tar_path:
            result = await self.podman(
                'image', 'import',
                *self._commit_options(),
                tar_path,
                _log=False)
        id = str(result.stdout, 'utf-8').strip()
        inspect_result = await self.podman(
            'image', 'inspect',
            id, _log=False)
        image_info = json.loads(str(inspect_result))[0]
        self.last_layer = id
        self.parse_base_image_info(image_info)


__all__ += ['PodmanFromScratchImage', 'podman_image_volume_key']


def image_layer_task(customization):
    '''Wrap a :class:`~carthage.machine.BaseCustomization` as a layer in a :class:`PodmanImage`.
    '''
    return ImageLayerTask(customization)

__all__ += ['image_layer_task']

class ImageLayerTask(TaskWrapperBase):

    customization: BaseCustomization
    description: str

    def __init__(self, customization, **kwargs):
        self.customization = customization
        description = getattr(customization, 'description',
                                   'Image Layer: ' + customization.__name__)
        super().__init__(description=description, **kwargs)

    async def func(self, image:PodmanImage):
        async with image.image_layer_context(self.description) as container:
            await container.apply_customization(self.customization)

    async def check_completed_func(self, image):
        # We always want to rerun images
        return False

    @memoproperty
    def stamp(self):
        #Overridden if an image_layer_task is assigned as a class property
        return self.customization.__name__


@inject_autokwargs(
    podman_options=InjectionKey('podman_options', _optional=NotPresent),
    )
class ContainerfileImage(OciImage, no_auto_inject=True):

    '''Build an image using ``podman build`` from a context directory with a ``Containerfile``.

    :param container_context: A directory with a Containerfile and potentially other files used by the Containerfile.  This can be specified either in a call to the constructor or in a subclass definition.  In the constructor, this is resolved relative to the current directory.  In a subclass, this is resolved relative to the package (or module) in which the class is defined.

    :param build_args: A dictionary; keys will be the name of ARGs and
    the value will be the value passed in with ``--build-arg``.  The
    dictionary will be run through resolve_deferred.  As a special
    case, if one of the values is an :class:`OciImage`, then value of
    the build_arg will be the image tag of that image.  This can be
    used for handling base images.

    This class does respect :class:`OciMount` and :class:`OciEnviron`
    in the injector hierarchy.

    '''

    #: Options to pass to podman
    podman_options = tuple()

    build_args: dict = {}

    def __init__(self, container_context=None, **kwargs):

        if container_context:
            self.container_context = container_context

        else:

            if not hasattr(self, 'container_context'):
                raise TypeError('container_context must be set on the class or in the constructor')

            # We look for the container_context directory relative to
            # the class providing the container_context attribute.
            # This is so if we derive from the class, as in:
            #
            #   class myimage(BaseImage):
            #       configvar = 'value'
            #
            # we get the container_context provided by BaseImage.  It
            # might be nice to allow container_contexts to overlay,
            # but we don't attempt that.

            container_context_class = None
            for k in type(self).__mro__:
                if 'container_context' in k.__dict__:
                    container_context_class = k
                    break
            if container_context_class is None:
                container_context_class = self.__class__

            module = None
            try:
                module = sys.modules[container_context_class.__module__]
            except Exception as e:
                warnings.warn(f'Unable to find module for {self.__class__.__qualname__}: {e}')

            # Why would we be unable to find the module?  But if we
            # don't, self.container_context needs to already be the full
            # path.

            if module:
                try: path = Path(module.__path__[0])
                except Exception:
                    path = Path(module.__file__).parent
                self.source_container_context = self.container_context = path/self.container_context

        # By this point, self.container_context has the full path
        super().__init__(**kwargs)
        self.container_host = None
        if len(self.setup_tasks) > 2:
            # More than just find_or_create and copy_context_if_needed
            self.setup_tasks.sort(key=lambda t: 1 if t.func == OciManaged.find_or_create.func else 0)
            self.container_context = self.stamp_path
        self.injector.add_provider(InjectionKey("podman_log"), self.log_path/'podman.log')

    @memoproperty
    def output_path(self):
        return self.stamp_path

    @property
    def stamp_subdir(self):
        return 'podman_image/'+self.oci_image_tag

    @setup_task("Copy Context if Needed", order=10)
    async def copy_context_if_needed(self):
        if len(self.setup_tasks) > 2:
            #More than just this task and find_or_create
            logger.info('copying container context for %s image', self.oci_image_tag)
            shutil.rmtree(self.output_path)
            shutil.copytree(self.source_container_context, self.output_path, symlinks=True)
        else:
            raise SkipSetupTask

    @copy_context_if_needed.invalidator()
    def copy_context_if_needed(self, last_run):
        source_mtime = self.container_context_mtime(self.source_container_context)
        if source_mtime > last_run: return False
        return True

    async def _handle_build_args(self):
        resolved_build_args = await resolve_deferred(self.ainjector, item=self.build_args, args={})
        result:list[str] = []
        for k,v in resolved_build_args.items():
            if isinstance(v,OciImage):
                await v.async_become_ready()
                v = v.oci_image_tag
            result.extend(['--build-arg', f'{k}={v}'])
        return result

    async def do_create(self):
        options = await self._build_options()
        options.extend(await self._handle_build_args())
        return await self.container_host.podman(
            'build',
            '--annotation', 'com.hadronindustries.carthage.image_mtime='+ \
            datetime.datetime.fromtimestamp(
                self.container_context_mtime(self.container_context),datetime.timezone.utc).isoformat(),
            '-t'+self.oci_image_tag,
            *options,
            self.container_context)

    async def find(self):
        if not self.container_host:
            await self.ainjector(instantiate_container_host, self)
        if not await self.container_host.find():
            logger.debug('%s does not exist because its container host does not exist', self)
            return False
        try: inspect_result = await self.container_host.podman(
                'image', 'inspect',
                self.oci_image_tag, _log=False)
        except sh.ErrorReturnCode: return False
        inspect_json = json.loads(str(inspect_result.stdout, 'utf-8'))
        created = dateutil.parser.isoparse(inspect_json[0]['Created']).timestamp()
        process_inspect_result(self, inspect_json[0])
        hadron_mtime_str = inspect_json[0]['Annotations'].get('com.hadronindustries.carthage.image_mtime')
        if hadron_mtime_str:
            hadron_mtime = dateutil.parser.isoparse(hadron_mtime_str).timestamp()
            if self.container_context_mtime(self.container_context) > hadron_mtime+5: return False
            return hadron_mtime
        if self.container_context_mtime(self.container_context) > created:
            return False
        return created

    @staticmethod
    def container_context_mtime(container_context):
        context = Path(container_context)
        mtime = 0.0
        for p in context.iterdir():
            stat = p.stat()
            if stat.st_mtime >mtime: mtime = stat.st_mtime
        return mtime



    async def _build_options(self):
        options = []
        from ..modeling import CarthageLayout
        layout = await self.ainjector.get_instance_async(InjectionKey(CarthageLayout, _optional=True))
        if layout_name := layout and layout.layout_name:
            options.extend(['--label', 'carthage.layout='+layout_name])
        # Instantiate a container simply so we can ask it for volume, mount, and environment options.
        with instantiation_not_ready():
            container = await self.ainjector(PodmanContainer, name='image_options', oci_container_image=self)
        for k, v in self.injector.filter_instantiate(
                OciEnviron, lambda k: 'name' in k.constraints and k.constraints.get('scope', 'all') in ('all','image')):
            options.append('--env')
            options.append(v.assignment)
        # podman build doesn't recognize --mount; we could remap some options to -v which is recognized.
        #for m in container.mounts:
            #options.append(await podman_mount_option(self.ainjector, m))
        options.extend(self.podman_options)
        return options

__all__ += ['ContainerfileImage']

class PodmanVolume(HasContainerHostMixin, OciManaged):

    name: str

    def __init__(self, name:str=None, **kwargs):
        self.container_host = None
        super().__init__(**kwargs)
        if name:
            self.name = name
        if not hasattr(self, 'name'):
            raise TypeError(f'{self.__class__.__name__} requires a name either specified in a subclass or constructor.')

    async def find(self):
        if not self.container_host:
            await self.ainjector(instantiate_container_host, self)
        if not await self.container_host.find():
            logger.debug(f'{self} does not exist because the container host does not exist.')
            return False
        try:
            result = await self.podman(
                'volume', 'inspect', self.name, _log=False)
        except sh.ErrorReturnCode:
            return False
        info = json.loads(str(result))[0]
        process_inspect_result(self, info)
        try:
            return dateutil.parser.isoparse(info['CreatedAt']).timestamp()
        except (KeyError, ValueError):
            logger.error('Unable to understand volume inspection result: %s', info)
            raise NotImplementedError('Podman too old')

    async def do_create(self):
        options = []
        from ..modeling import CarthageLayout
        layout = await self.ainjector.get_instance_async(InjectionKey(CarthageLayout, _optional=True))
        if layout_name := layout and layout.layout_name:
            options.extend(['--label', 'carthage.layout='+layout_name])
        return await self.podman(
            'volume', 'create',
            *options, self.name)

    async def find_or_create(self):
        '''
        Not a setup_task so that setup_tasks can be run with volume mounted.
        '''
        if self.readonly:
            return await self.find()
        return await super().find_or_create()
    
    async def delete(self):
        await self.podman(
            'volume', 'rm',
            self.name)

    async def async_ready(self):
        '''
        Run setup_tasks with self mounted.
        '''
        logger.info('Finding PodmanVolume %s', self.name)
        await self.find_or_create()
        result = await self.run_setup_tasks(context=self._setup_task_context())
        # bypass SetupTaskMixin
        await AsyncInjectable.async_ready(self)
        return result

    @contextlib.asynccontextmanager
    async def _setup_task_context(self):
        try:
            async with self.filesystem_access() as path:
                self.path = path
                yield
        finally:
            self.path = None
    @memoproperty
    def stamp_subdir(self):
        return 'podman_volume/'+self.name

    @property
    def podman(self):
        return self.container_host.podman

    @contextlib.asynccontextmanager
    async def filesystem_access(self):
        '''
        Like :meth:`carthage.Machine.filesystem_access` except gains access to a podman volume.

        Usage::

            async with volume.filesystem_access() as path:
                 # Path points to a mount for the volume inside the context manager.
        '''
        if not self.container_host:
            await self.find()
        async with self.container_host.filesystem_access_volume(
                self.name) as path:
            yield path

__all__ += ['PodmanVolume']

@inject(ainjector=AsyncInjector,
        container_host=podman_container_host,
        )
async def cache_podman_image(tag_func, factory, *, ainjector, container_host):
    '''
        Call *tag_func*. This function should return a dict containing at minimum *oci_image_tag*.
        Checks to see if the image with that tag is already cached. If so, returns that.
        If not, calls *factory* passing in the dict from *tag_func* and instantiates and caches the image.
        '''
    tag_kwargs  = await ainjector(tag_func)
    oci_image_tag = tag_kwargs['oci_image_tag']
    container_host = await construct_container_host(container_host=container_host, ainjector=ainjector)
    image_key = InjectionKey(OciImage, oci_image_tag=oci_image_tag)
    try:
        image = await container_host.ainjector.get_instance_async(image_key)
        return image
    except KeyError:
        pass
    image = await ainjector(factory, **tag_kwargs)
    container_host.injector.add_provider(image_key, image)
    return await container_host.ainjector.get_instance_async(image_key)

__all__ += ['cache_podman_image']

