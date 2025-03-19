# Copyright (C)  2024, 2025, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import asyncio
import os
import shlex
import typing
import carthage.machine
from . import machine, sh
from .utils import memoproperty
from .ssh import SshAgent

__all__ = []

#: List of sftp-server locations
sftp_server_locations = (
    '/usr/lib/sftp-server',
    '/usr/libexec/openssh/sftp-server',
    '/usr/lib/carthage-sftp-server', #See podman_sftp_server_mount
    )

def SFTP_SERVER_COMMAND(prefix):
    return f"cd /; for sftp in {' '.join(sftp_server_locations)} ; do test -x $sftp && exec {prefix} $sftp; done"

class BecomePrivilegedMixin(machine.Machine):

    '''
    Add ``sudo`` support to a :class:`~carthage.machine.Machine`.  For :meth:`run_command`, :meth:`filesystem_access`, and :func:`carthage.ssh.rsync`, use ``sudo`` to be come :attr:``runas_user` if :attr:`runas_user` differs from :attr:`ssh_login_user`.
    The run_command implementation in this class is only useful for classes that depend on ssh. If there is another mechanism to run commands that does not directly support selecting a user, it is necessary to adjust the MRO so that this class comes before that implementation, and add some sort of _cwd support to run_command (and use it in this implementation).

    '''


    def become_privileged(self, user):
        '''
        Returns True if we need to use sudo to run as the given user.
        '''
        return user != self.ssh_login_user


    def become_privileged_command(self, user):
        '''
        If become_privileged is False, this is the empty list.  Else, it is a list of command (and arguments) to be included in an ssh or shell invocation.
        '''
        if not self.become_privileged(user):
            return []
        else:
            return ['sudo', '-u', user]

    async def run_command(self, *args, _bg=True, _bg_exc=False, _user=None,
                          **kwargs):
        if _user is None:
            _user = self.runas_user
        if not self.become_privileged(_user):
            return await super().run_command(*args, _user=_user, **kwargs)
        return await self.ssh(
            'cd / &&',
            *self.become_privileged_command(_user),
            shlex.join([str(a) for a in args]), **kwargs)

    async def sshfs_process_factory(self, user):
        become_privileged_command = self.become_privileged_command(user)
        if not become_privileged_command:
            return await super().sshfs_process_factory(user)
        return await sshfs_sftp_finder(
            self,
            become_privileged_command=become_privileged_command,
            sshfs_path=self.sshfs_path,
            prefix="")

__all__ += ['BecomePrivilegedMixin']

async def sshfs_sftp_finder(
        machine:                            machine.Machine,
                            become_privileged_command: list,
        sshfs_path: str,
        prefix:str = ""):
    '''Like :class:`Machine`.  Does not use the sftp subsystem,
    but instead tries to find an sftp server.  Also, mostly for
    podman's convenience in running an sftp server with unshare,
    supports a prefix argument.

    :param prefix: Command inserted between the become_privileged_command and sftp invocation.  Can be used to enter the right namespace.

    '''
    agent = await machine.ainjector.get_instance_async(SshAgent)
    sftp_command_list = become_privileged_command + [
        '/bin/sh', '-c',
        SFTP_SERVER_COMMAND(prefix)]
    sftp_command =shlex.join(sftp_command_list)
    return sh.sshfs(
        '-o' 'ssh_command=' + " ".join(
                str(machine.ssh).split()[:-1]),
        '-osftp_server='+sftp_command,
        f'{carthage.machine.ssh_user_addr(machine)}:/',
        sshfs_path,
        '-f',
        _env=agent.agent_environ)

async def sshfs_to_sftp_server(sshfs_path:str, prefix:list[str], ):
    '''
    Run sshfs -opassive connected over stdin and stdout to an sftp server. Useful for getting filesystem access in different privilege contexts.

    :param prefix: A set of command to prefix to a shell fragment that finds the sftp-server.  Often the last components of prefix are ``'sh', '-c'``.

    '''
    sftp_stdin, sshfs_stdout  = os.pipe()
    sshfs_stdin, sftp_stdout = os.pipe()
    try:
        sshfs = sh.sshfs(
            '-opassive',
            'machine:/', sshfs_path,
            _bg=True,
            _in=sshfs_stdin,
            _out=sshfs_stdout)
        sftp_command = sh.Command(prefix[0])
        sftp = sftp_command(
            *prefix[1:], SFTP_SERVER_COMMAND(''),
            _bg=True, _bg_exc=True,
            _in=sftp_stdin,
            _out=sftp_stdout)
        return sshfs
    finally:
        for fd in sshfs_stdin, sshfs_stdout, sftp_stdin, sftp_stdout:
            os.close(fd)
