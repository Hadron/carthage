# Copyright (C) 2019, 2020, 2021, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import requests
import shutil

from pyVmomi import vim, vmodl
from pathlib import Path
from carthage import *
from carthage.console import *

from .inventory import VmwareSpecifiedObject
from .folder import VmwareFolder


class DataStoreFolder(VmwareFolder, kind='datastore'):
    pass


class VmwareDataStoreCluster(VmwareSpecifiedObject, kind='datastore'):

    parent_type = DataStoreFolder

    async def do_create(self):
        self.parent.mob.CreateStoragePod(name=self.name)

    async def delete(self):
        task = self.mob.Destroy_Task()
        await carthage.vmware.utils.wait_for_task(task)


class VmwareDataStore(VmwareSpecifiedObject, kind='datastore'):

    '''
    If *local_path* is set it is either a path to a local NFS mount of the datastore, or a ``host:path`` commbination in which case the data store is accessed by scp.
'''

    parent_type = (VmwareDataStoreCluster, DataStoreFolder)

    def __init__(self, name, *, local_path=None, host=None, hosts=None, spec=None, **kwargs):

        self.spec = spec
        if (hosts is not None) and (host is not None):
            raise ValueError('specify only one of host or hosts')
        elif (hosts is None) and (host is not None):
            self.hosts = [host]
        elif (hosts is not None) and (host is None):
            self.hosts = None
        else:
            # None here means that we should use what we find.
            self.hosts = hosts
        if not name.startswith('/') and 'parent' not in kwargs:
            vmc = kwargs['config_layout'].vmware
            kwargs['parent'] = f'/{vmc.datacenter}/datastore'
            self.parent_type = DataStoreFolder
        kwargs['name'] = name
        self.local_path = local_path
        super().__init__(**kwargs)

    async def do_create(self):

        if self.hosts is None:
            raise ValueError(f'must specify host(s) when creating datastore {self.name}')
        if len(self.hosts) != 1:
            raise NotImplementedError(
                f'support for multiple hosts is not yet implemented when creating datastore {self.name}')

        ds = self.hosts[0].mob.configManager.datastoreSystem.CreateNasDatastore(spec=self.spec)
        return
        try:
            task = self.parent.mob.MoveIntoFolder_Task([ds])
            await carthage.vmware.utils.wait_for_task(task)
        except BaseException:
            ds.DestroyDatastore()
            raise

    async def delete(self):
        task = self.mob.Destroy_Task()
        await carthage.vmware.utils.wait_for_task(task)

    #: List of ssh options to use when contacting a remote host
    ssh_opts = ('-oStrictHostKeyChecking=no', )

    @memoproperty
    def ssh_host(self):
        host, sep, path = self.local_path.partition(":")
        if sep != "":
            return host

    async def makedirs(self, dest):
        if self.ssh_host:
            host, sep, folder = self.local_path.partition(":")
            folder = Path(folder) / dest
            folder = folder.replace('"', '\\"')
            await sh.ssh(self.ssh_opts,
                         host, "mkdir", "-p",
                         f'"{folder}"',
                         _bg=True,
                         _bg_exc=False)
        else:
            os.makedirs(Path(self.local_path) / dest, exist_ok=True)

    def upload_file(self, path, dest):
        logger.info(f'Uploading {path} to {self.name}')
        params = dict(
            dsName=self.name,
            dcPath=self.config_layout.vmware.datacenter)
        verify = self.config_layout.vmware.validate_certs
        host = self.config_layout.vmware.hostname
        url = f'https://{host}:443/folder/{dest}'
        with open(path, "rb") as f:
            resp = requests.put(url, params=params,
                                data=f, auth=self.auth_tuple,
                                verify=verify)
            if resp.status_code >= 400:
                raise ValueError(f'Uploading {basename} failed: {resp.status_code}')

    async def copy_in(self, src, dest):
        '''
        Copy files into the datastore

        Copy *src* to *dest* within the datastore.  *dest* must be a relative path.
        '''
        src = Path(src)
        dest = Path(dest)
        if dest.is_absolute():
            raise ValueError("Destination cannot be absolute")
        if self.local_path and self.ssh_host:
            local_path = self.local_path
            await self.makedirs(dest.parent)
            logger.debug(f'copying {src} to {local_path}/{dest}')
            await sh.scp(self.ssh_opts, src, f'{local_path}/{dest}',
                         _bg=True, _bg_exc=False)
        elif self.local_path:
            await self.makedirs(dest.parent)
            await asyncio.get_event_loop().run_in_executor(None, shutil.copy, src, Path(self.local_path) / dest)
        else:
            await asyncio.get_event_loop().run_in_executor(None, self.upload_file, src, str(dest))

    @memoproperty
    def auth_tuple(self):
        return (self.config_layout.vmware.username, self.config_layout.vmware.password)
