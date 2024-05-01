# Copyright (C) 2018, 2019, 2020, 2021, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import contextlib
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from .dependency_injection import *
from .image import SetupTaskMixin, setup_task, SkipSetupTask, ContainerVolume
from . import sh, ConfigLayout
from .utils import memoproperty
from .machine import MachineRunning, Machine, SshMixin, ssh_origin
import carthage.network
import carthage.ssh

_resources_path = Path(__file__).parent.joinpath("resources")
logger = logging.getLogger('carthage.container')


container_image = InjectionKey('container-image')
container_volume = InjectionKey('container-volume')


@inject(image=InjectionKey(container_image, _ready=False),
        loop=asyncio.AbstractEventLoop,
        config_layout=ConfigLayout,
        network_config=InjectionKey(carthage.network.NetworkConfig, optional=True),
        injector=Injector)
class Container(Machine, SetupTaskMixin):

    def __init__(self, name, *, network_config,
                 skip_ssh_keygen=False, **kwargs):
        super().__init__(
            name=name, **kwargs)
        self.process = None
        self.skip_ssh_keygen = skip_ssh_keygen
        self.running = False
        self._operation_lock = asyncio.Lock()
        self._out_selectors = []
        self._done_waiters = []
        self.container_running = self.machine_running
        self.network_namespace = None
        self.close_volume = True
        self.cleanup_future = None

    rsync_uses_filesystem_access = True

    async def async_ready(self):
        try:
            vol = await self.ainjector.get_instance_async(container_volume)
            self.close_volume = False
        except KeyError:
            await self.image.async_become_ready()
            vol = await self.ainjector(ContainerVolume,
                                       clone_from=self.image,
                                       name="containers/" + self.name)
            self.injector.add_provider(container_volume, vol)
        self.volume = vol
        await self.resolve_networking()
        await self.is_machine_running()
        await self.run_setup_tasks()
        return await super().async_ready()

    async def is_machine_running(self):
        try:
            self.container_leader
            self.running = True
        except sh.ErrorReturnCode_1:
            self.running = False
        return self.running

    @memoproperty
    def stamp_path(self):
        if self.volume is None:
            raise RuntimeError('Volume not yet created')
        return self.volume.path

    async def do_network_config(self, networking):
        if networking and self.network_links:
            namespace = carthage.network.NetworkNamespace(self.full_name, self.network_links)
            try:
                await namespace.start_networking()
                self.network_namespace = namespace
                return ["--network-namespace-path=/run/netns/" + namespace.name,
                        "--capability=CAP_NET_ADMIN",
                        "--resolv-conf=off"]
            except BaseException:
                namespace.close()
                raise
        else:
            return ["--resolv-conf=" + self.config_layout.host_networking_resolv_conf]

    async def run_container(self, *args, raise_on_running=True,
                            networking=False,
                            as_pid2=True):
        async with self._operation_lock:
            if self.running:
                if raise_on_running:
                    raise RuntimeError('{} already running'.format(self))
                return self.process
            if self.cleanup_future:
                try:
                    await self.cleanup_future
                except BaseException:
                    pass
            net_args = await self.do_network_config(networking)
            if as_pid2:
                net_args.insert(0, '--as-pid2')
            if networking:
                await self.start_dependencies()
            self.cleanup_future = self.done_future()
            logger.info("Starting container {}: {}".format(
                self.name,
                " ".join(args)))
            if hasattr(self, 'model') and hasattr(self.model, 'container_args'):
                net_args = self.model.container_args + net_args
            if hasattr(self, 'container_args'):
                net_args = self.container_args + net_args
            # Move systemd options forward
            to_delete = 0
            for a in args:
                if a.startswith('--'):
                    to_delete += 1
                    net_args.insert(0, a)
                else:
                    break
            args = args[to_delete:]
            self.process = sh.systemd_nspawn("--directory=" + str(self.volume.path),
                                             '--machine=' + self.full_name,
                                             "--setenv=DEBIAN_FRONTEND=noninteractive",
                                             *net_args,
                                             *args,
                                             _bg=True,
                                             _bg_exc=False,
                                             _done=self._done_cb,
                                             _out=self._out_cb,
                                             _err_to_out=True,
                                             _tty_out=True,
                                             _in="/dev/null",
                                             _encoding='utf-8',
                                             _new_session=False,
                                             _env=self._environment(networking),
                                             )

            self.running = True
            return self.process

    async def stop_container(self):
        async with self._operation_lock:
            if not self.running:
                return
            if self.process is not None:
                self.process.terminate()
                process = self.process
                self.process = None
                try:
                    await process
                except sh.SignalException_SIGTERM:
                    pass
            else:
                await sh.machinectl("stop", self.full_name,
                                    _bg=True, _bg_exc=False)
                try:
                    await sh.ip("netns", "del", self.full_name,
                                _bg=True, _bg_exc=False)
                except BaseException:
                    pass
                self._done_cb(code=0, success=True, cmd=None)
            await super().stop_machine()

    stop_machine = stop_container

    def _done_cb(self, cmd, success, code):
        def callback():
            # Callback needed to run in IO loop thread because futures
            # do not trigger their done callbacks in a threadsafe
            # manner.
            for f in self._done_waiters:
                if not f.cancelled():
                    f.set_result(0 if success else code)
            self._done_waiters = []
            if self.network_namespace:
                self.network_namespace.close()
            self.network_namespace = None
        logger.info("Container {} exited with code {}".format(
            self.name, code))
        for k in ('shell', 'container_leader'):
            try:
                del self.__dict__[k]
            except KeyError:
                pass
        self.running = False
        self.loop.call_soon_threadsafe(callback)

    def done_future(self):
        future = self.loop.create_future()
        self._done_waiters.append(future)
        return future

    def _out_cb(self, data):
        data = data.strip()
        logger.debug("Container {}: output {}".format(self. name,
                                                      data))

        for selector in self._out_selectors:
            r, cb, once = selector
            if cb is None:
                continue
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
        def started_callback(m, data):
            started_future.set_result(True)
        if self.running:
            return
        started_future = self.loop.create_future()
        self.find_output(r'\].*Reached target.*Basic System', started_callback, True)
        # run_container calls start_dependencies
        await super().start_machine()
        await self.run_container("--kill-signal=SIGRTMIN+3", *args, "/bin/systemd",
                                 networking=True, as_pid2=False,
                                 raise_on_running=False)
        done_future = self.done_future()
        await asyncio.wait([done_future, started_future],
                           return_when="FIRST_COMPLETED")
        if done_future.done():
            logger.error("Container {} failed to start".format(self.name))
            raise RuntimeError("Container failed to start")
        assert started_future.result() is True
        logger.info("Container {} started".format(self.name))

    start_machine = start_container

    @setup_task('ssh-keygen')
    async def generate_ssh_keys(self):
        if self.skip_ssh_keygen:
            raise SkipSetupTask
        process = await self.run_container("/usr/bin/ssh-keygen", "-A")
        await process
        self.ssh_rekeyed()

    async def container_command(self, *args, _bg=True, _bg_exc=False, **kwargs):
        '''Call :meth:`run_container` and await the resulting process.
        '''
        process = await self.run_container(*args, **kwargs)
        return await process

    def close(self, canceled_futures=None):
        if hasattr(self, 'volume'):
            if self.close_volume:
                self.volume.close()
            del self.volume
            self.injector.close(canceled_futures=canceled_futures)

    def __del__(self):
        self.close()

    async def network_online(self):
        await self.shell('/bin/systemctl', "start", "network-online.target",
                         _bg=True, _bg_exc=False
                         )

    @memoproperty
    def container_leader(self):
        return str(sh.machinectl('-pLeader', '--value', 'show', self.full_name,
                                 _in="/dev/null",
                                 _tty_out=False, _bg=False
                                 ).stdout,
                   'utf-8').strip()

    @memoproperty
    def shell(self):
        # You might think you want to use machinectl shell to create a shell.
        # That might be nice, except that you don't get exit values so you don't
        # know if your shell commands succeed or not.
        if not self.running:
            raise RuntimeError("Container not running")

        return sh.nsenter.bake("-t" + self.container_leader, "-C", "-m", "-n", "-u", "-i", "-p",
                               _env=self._environment())

    def run_command(self, *args, _bg=True,
                          _bg_exc=False, _user=None,
                          **kwargs):
        if _user is None:
            _user = self.runas_user
        if _user != 'root':
            raise NotImplementedError('Not currently supported for runas_user to be different than root')
        if self.running:
            return self.shell(*args, **kwargs)
        else:
            return self.container_command(*args, _bg=_bg, _bg_exc=_bg_exc, **kwargs)
        
    def _environment(self, networking=False):
        env = os.environ.copy()
        env['DEBIAN_FRONTEND'] = 'noninteractive'
        if networking:
            env['SYSTEMD_NSPAWN_API_VFS_WRITABLE'] = 'network'
        return env

    @contextlib.asynccontextmanager
    async def filesystem_access(self):
        yield Path(self.volume.path)

    @contextlib.asynccontextmanager
    async def ansible_not_running_context(self):
        '''
        When a :class:`Container`  that is not running is used as a host in a call to :func:`carthage.ansible.run_playbook`, then the ansible *chroot* plugin is used to connect to the container.  However, configuration is changed to use ``nsenter`` so that an actual namespace is used.
'''
        process = await self.run_container("/bin/bash")  # create a container; the bash does nothing
        try:
            yield {
                'ansible_connection': 'chroot',
                # Overriding ansible_executable is a hack; it's one of the arguments we
                # can specify and we need to pass the name to our helper
                'ansible_executable': self.full_name,
                'ansible_host': str(self.volume.path),
                'ansible_chroot_exe': str((_resources_path / "ansible-chroot-helper").absolute()),
            }
        finally:
            try:
                await self.stop_container()
            except Exception:
                pass

    def _apply_to_container_customization(self, customization):
        '''A method indicating that this object can have :class:`~carthage.machine.ContainerCustomizations` applied.  Provides a mechanism for adapting the customization if needed to a particular container-like machine.  Not needed for this class.
        '''
        return

    def _apply_to_filesystem_customization(self, customization):
        customization.path = self.volume.path

__all__ = ['container_volume', 'container_image', 'Container']
