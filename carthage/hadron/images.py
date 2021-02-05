import asyncio, os, os.path, pkg_resources, shutil, sys, yaml
from ..image import ContainerImage, setup_task, SetupTaskMixin, ImageVolume, ContainerImageMount, SshAuthorizedKeyCustomizations
from ..container import Container, container_volume, container_image
from ..dependency_injection import *
from ..config import ConfigLayout
from .. import sh, ansible, pki
from ..utils import when_needed
from ..machine import Machine, ContainerCustomization, customization_task, ssh_origin
import carthage.ssh
import carthage.network
import carthage.pki
_resources_path = os.path.join(os.path.dirname(__file__), "../resources")

class HadronImageMixin(ContainerCustomization):

    description = "Hadron Image Customizations"

    @setup_task('Enable ACES and set release')
    @inject(config = ConfigLayout)
    async def setup_hadron_packages(self, config):
        ainjector = self.injector(AsyncInjector)
        try:
            bind_mount = '--bind-ro='+self.config_layout.hadron_operations+":/hadron-operations"
            await self.container_command('/bin/systemctl', 'disable', 'sddm', 'systemd-networkd', 'systemd-resolved', 'systemd-networkd.socket')
            await self.container_command(bind_mount, "/usr/bin/apt", "update")
            await self.container_command(bind_mount, "/usr/bin/apt",
                                         "install", "-y", "ansible",
                                         "git", "python3-pytest",
                                         "ca-certificates",
                                         "python-apt", "haveged"
            )
            await self.container_command(bind_mount, "/usr/bin/ansible-playbook",
                                         "-clocal",
                                         "-ehadron_os=ACES",
                                         "-ehadron_track=proposed",
                                         "-epackagedir=/hadron-operations/ansible/packages",
                                         f"-ehadron_release={config.hadron_release}",
                                         f"-eaces_apt_server={config.aces_mirror}",
                                         "-i/hadron-operations/ansible/localhost-debian.txt",
                                         "/hadron-operations/ansible/commands/hadron-packages.yml"
            )
            await self.container_command("/usr/bin/apt", "update")
            await self.container_command(
                #'--bind-ro=/bin/true:/usr/sbin/update-grub',
                                                    '/usr/bin/apt', '-y', '--allow-downgrades', 'dist-upgrade')
            await self.container_command('/usr/bin/apt', 'install', '-y',
                                         'hadron-container-image', 'python3-photon')
        finally: pass

    ssh_authorized_keys = customization_task(SshAuthorizedKeyCustomizations)

    pki_customizations = customization_task(pki.PkiCustomizations)
    @setup_task('hadron-xorg-modes')
    def install_xorg_modes(self):
        os.makedirs(os.path.join(self.path,
                                 "etc/X11/xorg.conf.d"), exist_ok = True)
        shutil.copy2(os.path.join(_resources_path, "hadron-xorg-modes"),
                     os.path.join(self.path, "etc/X11/xorg.conf.d/10-hadron-modes.conf"))



class HadronContainerImage(ContainerImage):

    def __init__(self, **kwargs):
        super().__init__(name = "base-hadron", **kwargs)


    hadron_customizations = customization_task(HadronImageMixin)

database_key = InjectionKey(Machine, host = 'database.hadronindustries.com')

@inject(
    config_layout = ConfigLayout,
    injector = Injector,
    loop = asyncio.AbstractEventLoop,
    image = container_image,
    network_config = carthage.network.NetworkConfig)
class TestDatabase(Container):

    def __init__(self, name = "test-database", **kwargs):
        super().__init__(name = name, **kwargs)
        self.injector.add_provider(database_key, dependency_quote(self))

    def start_machine(self):
        return self.start_container('--capability=cap_bpf', '--system-call-filter=bpf')

    ansible_inventory_name = "database.hadronindustries.com"


    @setup_task("install-db")
    async def install_packages(self):
        with open(self.volume.path+"/etc/network/interfaces", "wt+") as f:
            #Convince NetworkManager to leave eth1 alone before internet-zone comes along
            f.write("iface eth1 inet manual\n")
            f.write("iface binternet inet manual\n")
            # And use dhcp from ifupdown rather than NetworkManager so we can get easy access to the nameservers
            f.write('iface eth0 inet dhcp\nauto eth0\niface eth2 inet manual\n')

        async with self.machine_running():
            await self.network_online()
            await self.shell("/usr/bin/apt-get", "update",
                             _bg = True, _bg_exc = False,
                             _out = self._out_cb,
                             _err_to_out = True)
            await self.shell("/usr/bin/apt",
                             "-y", "install", "hadron-inventory-admin",
                             "hadron-photon-admin",
                             "hadron-carthage-cli",
                             "resolvconf",
                             "socat",
                             "hadron-ansible",
                             _in = "/dev/null",
                             _out = self._out_cb,
                             _err_to_out = True,
                             _bg = True, _bg_exc = False)

    @inject(ssh_key = carthage.ssh.SshKey,
            pki = carthage.pki.PkiManager,
            host_map = carthage.network.host_map_key)
    @setup_task('clone-hadron-ops')
    async def clone_hadron_operations(self, ssh_key, pki, host_map):
        config = self.config_layout
        mirror_addr_result = await self.loop.getaddrinfo(config.aces_mirror, None, proto=6)
        aces_mirror_address = mirror_addr_result[0][4][0]
        host_map[config.aces_mirror] = carthage.network.HostMapEntry(ip = aces_mirror_address)
        await sh.git('bundle',
                     'create', self.volume.path+"/hadron-operations.bundle",
                     "HEAD",
                     "master",
                     _bg = True, _bg_exc = False,
                     _cwd = self.config_layout.hadron_operations)
        process = await self.run_container('/usr/bin/git',
                                           'clone', '--branch=master',
                                           '/hadron-operations.bundle')
        await process
        hadron_ops = os.path.join(self.volume.path, "hadron-operations")
        with open(os.path.join(
                hadron_ops,
                "config/test.yml"), "wt") as f:
            f.write(yaml.dump({
                'carthage_key': ssh_key.pubkey_contents,
                "aces_apt_server": str(config.aces_mirror),
                "expose_routes": config.expose_routes + [aces_mirror_address],
                "host_map": {
                    name: [e.ip, e.mac] for name, e in host_map.items()}},
                              default_flow_style = False))

        os.unlink(os.path.join(self.volume.path, 'hadron-operations.bundle'))
        with open(os.path.join(self.volume.path,
                               "hadron-operations/ansible/resources/cacerts/carthage.pem"), "wt") as f:
            f.write(pki.ca_cert)
            os.truncate(os.path.join(hadron_ops, "config/ipsec_mesh.yaml"), 0)

    @setup_task('copy-database')
    async def copy_database_from_master(self):
        "Copy the master database.  Run automatically.  Could be run agains if hadroninventoryadmin is locally dropped and recreated"
        async with self.machine_running():
            self.injector.add_provider(ssh_origin, self)
            await self.network_online()
            await asyncio.sleep(5)
            env = os.environ
            await self.shell("/bin/touch", "/hadron-operations/config/no_vault",
                             _bg = True, _bg_exc = False)
            env['PYTHONPATH'] = "/hadron-operations"
            await self.shell( '/usr/bin/python3',
                              '-mhadron.inventory.config.update',
                              '--copy=postgresql:///hadroninventoryadmin',
                              '--copy-users',
                              _bg = True,
                              _bg_exc = False,
                              _out = self._out_cb,
                              _err_to_out = True,
                              _env = env)


    @setup_task('make-update')
    async def make_update(self):
        "Run make update in /hadron-operations; can be repeated as needed"
        async with self.machine_running():
            await self.network_online()
            from .database import fixup_database
            await self.ainjector(fixup_database)
            await self.shell('/bin/sh', '-c',
                             "cd /hadron-operations&&PULL_OPTS='--database postgresql:///hadroninventoryadmin' make update",
                             _out = self._out_cb,
                             _err_to_out = True,
                             _bg = True,
                             _bg_exc = False)
            await self.shell("/bin/sh", "-c",
                             "cd /hadron-operations/ansible&&ansible-playbook -c local commands/test-database.yml",
                             _bg = True, _bg_exc = False,
                             _out = self._out_cb,
                             _err_to_out = True)

    def ansible(self, host_pattern, play,
                      *, log_to = None,
                      tags = [], args = None):
        extra_args = []
        if args: extra_args.append(args)
        if tags:
            extra_args.append("--tags="+",".join(tags))
        if log_to is None: log_to = self
        log_file = os.path.join(log_to.stamp_path, "ansible.log")
        play = os.path.join("/hadron-operations/ansible", play)
        return self.ainjector(
            ansible.run_playbook,
            host_pattern,play,
            "/hadron-operations/ansible/inventory/hosts.txt",
            extra_args = extra_args,
            origin = self,
            log = log_file)
    
            

    ip_address = "192.168.101.1"


hadron_container_image = when_needed(HadronContainerImage)


class HadronVmImage(ImageVolume):

    def __init__(self, *, name = "base-hadron-vm",
                 path = None, **kwargs):
        if path is not None: kwargs['path'] = path
        if 'create_size' not in kwargs:
            kwargs['create_size'] = kwargs['config_layout'].vm_image_size
        super().__init__(name,
                             **kwargs)




    @setup_task('resize-disk')
    async def resize_disk(self):
        ainjector = await self.ainjector(AsyncInjector)
        try:
            mount = None
            mount = await ainjector(ContainerImageMount, self)
            ainjector.add_provider(container_volume, mount)
            ainjector.add_provider(container_image, mount)
            container = await ainjector(Container, name = self.name,
                                        skip_ssh_keygen = True)
            rootdev = mount.mount.rootdev
            loopdev = mount.mount.loopdev
            process = await container.run_container(
                '--bind='+ loopdev, '--bind='+ rootdev,
                '--bind=/bin/true:/usr/sbin/update-grub',
                '/usr/sbin/hadron-firstboot', '--no-ssh', '--no-hostname')
            await process
            mount.unmount_image()
            mount.mount_image()
            sh.btrfs('filesystem', 'resize', 'max', mount.mount.rootdir)
        finally:
            if mount is not None:             mount.close()

    hadron_customizations = customization_task(HadronImageMixin)

    @setup_task("Install udev rules for bridges")
    def install_udev_rules(self):
        from hadron.allspark.imagelib import image_mounted
        with image_mounted(self.path) as i:
            with open(os.path.join(i.rootdir,
                               "etc/udev/rules.d/80-net-setup-link.rules"), "wb") as f:
                f.write(
                    pkg_resources.resource_stream("carthage", "resources/80-net-setup-link.rules").read())
        
    @setup_task("Run update-grub")
    async def run_update_grub(self):
        from hadron.allspark.imagelib import image_mounted
        with image_mounted(self.path) as i:
            try:
                with open(os.path.join(i.rootdir, "run.sh"), "wt") as f:
                    f.write('''\
#!/bin/sh
set -e
sed -i -e 's:GRUB_CMDLINE_LINUX=.*$:GRUB_CMDLINE_LINUX="random.trust_cpu=on net.ifnames=0 console=ttyS0,115200n81 console=tty1":' /etc/default/grub
/usr/sbin/update-grub
''')
                    os.chmod(f.fileno(), 0o755)

                i.chroot("/run.sh")
            finally:
                try: os.unlink(os.path.join(i.rootdir, "run.sh"))
                except FileNotFoundError: pass


class HadronVaultContainer(Container):

    def __init__(self, name = "vault.hadronindustries.com",
                 **kwargs):
        super().__init__(name = name, **kwargs)

    @setup_task("Bootstrap vault")
    @inject(db = database_key)
    async def bootstrap_vault(self, db):
        from os.path import join as j
        def capture(f):
            if os.path.exists(j(target_path, f)):
                return False
            shutil.copyfile(j(source_path, f), j(target_path, f))
            with open(j(target_path, f), "rt") as f:
                return f.read()

        async with self.machine_running(ssh_online = True):
            await db.ssh("mkdir -p /vault_bootstrap",
                         _bg = True, _bg_exc = True)
            await db.ansible(self.name,
                             "hosts/vault.yml",
                             log_to = self,
                             args ='-evault_bootstrap_dir=/vault_bootstrap',
                             tags = ['bootstrap_vault'])
            async with db.filesystem_access() as db_path, self.filesystem_access() as fs_path:
                target_path = fs_path
                source_path = j(db_path, "vault_bootstrap")
                token = capture("token")
                if token:
                    capture("ca.pem")
                    capture("key.0")
                    await self.ainjector(
                        ansible.run_playbook,
                        [self, db, ansible.localhost_machine],
                        pkg_resources.resource_filename("carthage.hadron", "resources/vault_config.yml"),
                        j(db_path, "hadron-operations/ansible/inventory/hosts.txt"),
                        log = j(self.stamp_path, "ansible.log"),
origin = ansible.NetworkNamespaceOrigin(db))


    def start_machine(self):
        return self.start_container("--capability=CAP_IPC_LOCK")

hadron_vault_key = InjectionKey(Machine, host = "vault.hadronindustries.com")





hadron_vm_image = when_needed(HadronVmImage)


__all__ = r'''
    hadron_vm_image database_key hadron_container_image
    HadronImageMixin HadronContainerImage TestDatabase
HadronVaultContainer hadron_vault_key
 HadronVmImage
'''.split()
