import asyncio, dataclasses, os, tempfile, yaml
from pathlib import Path
from .dependency_injection import *
from .network import NetworkConfig, NetworkLink
from .machine import AbstractMachineModel
from . import sh, ConfigLayout
import carthage.ssh

__all__ = []

@dataclasses.dataclass
class CloudInitConfig:

    meta_data: dict = dataclasses.field(default_factory = lambda: {})
    user_data:dict = dataclasses.field( default_factory = lambda: {
        'disable_root': False})

    network_configuration: dict = dataclasses.field( default_factory = lambda: {})

    __all__ += ['CloudInitConfig']
    
@inject_autokwargs(model = AbstractMachineModel)
class CloudInitPlugin(Injectable):

    async def apply(self, config: CloudInitConfig):
        "Apply changes to the given :class:`CloudInitConfig`.  This is typically overridden so the plugin actually does something."
        pass

    @classmethod
    def default_class_injection_key(cls):
        return InjectionKey(CloudInitPlugin, name = cls.name)

    @property
    def name(self):
        raise NotImplementedError

__all__ += ['CloudInitPlugin']

@inject(model = AbstractMachineModel, ainjector = AsyncInjector,
        config_layout = ConfigLayout)
async def generate_cloud_init_cidata(
        *, model, ainjector, config_layout):
    '''

    Generates a cidata ISO imagebased on the provided model.  Operation is based on *cloud_init* in the model:

    * True: Generate cidata based on calling all the :class:`CloudInitPlugins <CloudInitPlugin>` registered with the model's injector.

    * False: return None

    * A :class:`CloudInitConfig` object: use that object without calling plugins.

    :return: Path to an ISO under the *stamp_path* of *model*

    '''
    cloud_init = getattr(model, 'cloud_init', False)
    if not cloud_init: return
    if cloud_init is True:
        config = CloudInitConfig()
        plugin_data = await ainjector.filter_instantiate_async(CloudInitPlugin, ['name'], ready = True)
        for plugin_key, plugin in plugin_data:
            # we apply in order so that plugins can look at previous
            # results.  We sacrifice parallelism to get this.
            await plugin.apply(config)
        if not plugin_data: return
    with tempfile.TemporaryDirectory(dir = config_layout.state_dir) as tmp_dir:
        tmp = Path(tmp_dir)
        with tmp.joinpath("user-data").open("wt") as f:
            f.write("#cloud-config\n")
            f.write(yaml.dump(config.user_data))
        with tmp.joinpath("meta-data").open("wt") as f:
            f.write(yaml.dump(config.meta_data))
        if config.network_configuration:
            config.network_configuration.update(version = 2)
            with tmp.joinpath("network-config").open("wt") as f:
                f.write(yaml.dump(config.network_configuration))
        output = Path(model.stamp_path)/"cloud_init.iso"
        output_tmp = Path(model.stamp_path)/"cloud_init.iso.tmp"
        await sh.genisoimage(
            "-J", "--rational-rock",
            "-o", output_tmp,
            "-V", "cidata",
            str(tmp),
            _bg = True, _bg_exc = False)
        os.rename(output_tmp, output)

    return output

__all__ += ['generate_cloud_init_cidata']

class NetworkPlugin(CloudInitPlugin):

    name = "network"

    async def apply(self, config: CloudInitConfig):
        ethernets = config.network_configuration.setdefault('ethernets', {})
        for l in self.model.network_links.values():
            if l.local_type: continue
            v4_config = l.merged_v4_config
            if l.member_of_links:
                member = l.member_of_links[0]
                if (member.member_links[0] == l ) and ( member.local_type in ('bridge', 'bond')):
                    v4_config = member.merged_v4_config
            if not (v4_config.dhcp or v4_config.address): continue
            eth_dict = dict()
            if l.mac:
                eth_dict['match'] = dict(macaddress = l.mac)
                eth_dict['set-name'] = l.interface
            if v4_config.dhcp: eth_dict['dhcp4'] = True
            else:
                eth_dict['dhcp4'] = False
                if v4_config.address:
                    eth_dict.setdefault('addresses', [])
                    eth_addresses = eth_dict['addresses']
                    eth_addresses.append(str(v4_config.address)+'/'+str(v4_config.network.prefixlen))

            ethernets[l.interface] = eth_dict

@inject_autokwargs(authorized_keys = carthage.ssh.AuthorizedKeysFile)
class AuthorizedKeysPlugin(CloudInitPlugin):

    name = "ssh_keys"
    
    async def apply(self, config: CloudInitConfig):
        authorized_keys_file= Path(self.authorized_keys.path)
        authorized_keys = list( filter(
            lambda k: k and (not k[0] == '#'),
            authorized_keys_file.read_text().split("\n")))
        config.meta_data['public_ssh_keys'] = authorized_keys

@inject(injector = Injector)
def enable_cloud_init_plugins(injector):
    injector.add_provider(AuthorizedKeysPlugin, allow_multiple = True)
    injector.add_provider(NetworkPlugin, allow_multiple = True)

__all__ += ['enable_cloud_init_plugins']
    