# Copyright (C) 2021, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import dataclasses
import os
import tempfile
import yaml
from pathlib import Path
from .dependency_injection import *
from .network import NetworkConfig, NetworkLink
from .machine import AbstractMachineModel
from . import sh, ConfigLayout
import carthage.ssh

__all__ = []


@dataclasses.dataclass
class CloudInitConfig:

    meta_data: dict = dataclasses.field(default_factory=lambda: {})
    user_data: dict = dataclasses.field(default_factory=lambda: {
        'disable_root': False})

    network_configuration: dict = dataclasses.field(default_factory=lambda: {})

    __all__ += ['CloudInitConfig']


class CloudInitPlugin(Injectable):

    async def apply(self, config: CloudInitConfig):
        "Apply changes to the given :class:`CloudInitConfig`.  This is typically overridden so the plugin actually does something."
        pass

    @classmethod
    def default_class_injection_key(cls):
        return InjectionKey(CloudInitPlugin, name=cls.name)

    @property
    def name(self):
        raise NotImplementedError


__all__ += ['CloudInitPlugin']


@inject(model=AbstractMachineModel,
        ainjector=AsyncInjector)
async def generate_cloud_init_cloud_config(*, ainjector, model):
    '''
    Generate a :class:`CloudInitConfig` from the model.  The operation is based on the *cloud_init* property in the model:

    * True: Generate cidata based on calling all the :class:`CloudInitPlugins <CloudInitPlugin>` registered with the model's injector.

    * False: return None

    * A :class:`CloudInitConfig` object: use that object without calling plugins.

    '''

    cloud_init = getattr(model, 'cloud_init', False)
    if not cloud_init:
        return
    if cloud_init is True:
        config = CloudInitConfig()
        plugin_data = await ainjector.filter_instantiate_async(CloudInitPlugin, ['name'], ready=True)
        for plugin_key, plugin in plugin_data:
            # we apply in order so that plugins can look at previous
            # results.  We sacrifice parallelism to get this.
            await plugin.apply(config)
        if not plugin_data:
            return
        return config
    else:
        return model.cloud_init

__all__ += ['generate_cloud_init_cloud_config']


@inject(model=AbstractMachineModel, ainjector=AsyncInjector,
        config_layout=ConfigLayout)
async def generate_cloud_init_cidata(
        *, model, ainjector, config_layout):
    '''

    Generates a cidata ISO imagebased on the provided model.  Operation is based on *cloud_init* in the model:

    * True: Generate cidata based on calling all the :class:`CloudInitPlugins <CloudInitPlugin>` registered with the model's injector.

    * False: return None

    * A :class:`CloudInitConfig` object: use that object without calling plugins.

    :return: Path to an ISO under the *stamp_path* of *model*

    '''
    config = await generate_cloud_init_cloud_config(ainjector=ainjector, model=model)
    if not config:
        return
    with tempfile.TemporaryDirectory(dir=config_layout.state_dir) as tmp_dir:
        tmp = Path(tmp_dir)
        with tmp.joinpath("user-data").open("wt") as f:
            f.write("#cloud-config\n")
            f.write(yaml.dump(config.user_data))
        with tmp.joinpath("meta-data").open("wt") as f:
            f.write(yaml.dump(config.meta_data))
        if config.network_configuration:
            config.network_configuration.update(version=2)
            with tmp.joinpath("network-config").open("wt") as f:
                f.write(yaml.dump(config.network_configuration))
        output = Path(model.stamp_path) / "cloud_init.iso"
        output_tmp = Path(model.stamp_path) / "cloud_init.iso.tmp"
        await sh.genisoimage(
            "-J", "--rational-rock",
            "-o", output_tmp,
            "-V", "cidata",
            str(tmp),
            _bg=True, _bg_exc=False)
        os.rename(output_tmp, output)

    return output

__all__ += ['generate_cloud_init_cidata']


@inject_autokwargs(model=AbstractMachineModel)
class NetworkPlugin(CloudInitPlugin):

    name = "network"

    async def apply(self, config: CloudInitConfig):
        ethernets = config.network_configuration.setdefault('ethernets', {})
        for l in self.model.network_links.values():
            if l.local_type:
                continue
            v4_config = l.merged_v4_config
            if l.member_of_links:
                member = l.member_of_links[0]
                if (member.member_links[0] == l) and (member.local_type in ('bridge', 'bond')):
                    v4_config = member.merged_v4_config
            if not (v4_config.dhcp or v4_config.address):
                continue
            eth_dict = dict()
            if l.mac:
                eth_dict['match'] = dict(macaddress=l.mac)
                eth_dict['set-name'] = l.interface
            if v4_config.dhcp:
                eth_dict['dhcp4'] = True
            else:
                eth_dict['dhcp4'] = False
                if v4_config.address:
                    eth_dict.setdefault('addresses', [])
                    eth_addresses = eth_dict['addresses']
                    eth_addresses.append(str(v4_config.address) + '/' + str(v4_config.network.prefixlen))

            ethernets[l.interface] = eth_dict


@inject_autokwargs(authorized_keys=carthage.ssh.AuthorizedKeysFile)
class AuthorizedKeysPlugin(CloudInitPlugin):

    name = "ssh_keys"

    async def apply(self, config: CloudInitConfig):
        authorized_keys_file = Path(self.authorized_keys.path)
        authorized_keys = list(filter(
            lambda k: k and (not k[0] == '#'),
            authorized_keys_file.read_text().split("\n")))
        config.meta_data['public_ssh_keys'] = authorized_keys
        config.user_data['ssh_authorized_keys'] = authorized_keys


@inject_autokwargs(model=AbstractMachineModel)
class HostnamePlugin(CloudInitPlugin):

    name = "hostname"

    async def apply(self, config: CloudInitConfig):
        config.user_data['hostname'] = self.model.name


@inject_autokwargs(authorized_keys=carthage.ssh.AuthorizedKeysFile)
class WriteAuthorizedKeysPlugin(CloudInitPlugin):

    '''
            This plugin uses the write_files module to write out root's authorized keys file.  It is not enabled by default because metadata is better when it is available.  But for example with EC2, this may be desirable to write out the full Carthage authorized_keys file without  dealing with EC2 keypairs.
    '''

    name = "write_authorized_keys"

    async def apply(self, config):
        write_files = config.user_data.setdefault('write_files', [])
        with open(self.authorized_keys.path, 'rt') as f:
            content = f.read()
        write_files.append(dict(
            path="/root/.ssh/authorized_keys",
            content=content,
            permissions='0644',
            owner='root:root'))

class DisableRootPlugin(CloudInitPlugin):

    name ='disable_root'

    async def apply(self, config):
        config.user_data['disable_root'] = True

__all__ += ['DisableRootPlugin']


@inject(injector=Injector)
def enable_cloud_init_plugins(injector):
    injector.add_provider(AuthorizedKeysPlugin, allow_multiple=True)
    injector.add_provider(NetworkPlugin, allow_multiple=True)
    injector.add_provider(HostnamePlugin, allow_multiple=True)


__all__ += ['enable_cloud_init_plugins']
