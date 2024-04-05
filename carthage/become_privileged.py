# Copyright (C)  2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import typing
from . import machine, sh
from .utils import memoproperty
__all__ = []

#: List of sftp-server locations
sftp_server_locations = (
    '/usr/lib/sftp-server',
    '/usr/libexec/openssh/sftp-server',
    )

class BecomePrivilegedMixin(machine.Machine):

    '''
    Add ``sudo`` support to a :class:`~carthage.machine.Machine`.  For :meth:`run_command`, :meth:`filesystem_access`, and :func:`carthage.ssh.rsync`, use ``sudo`` to be come :attr:``runas_user` if :attr:`runas_user` differs from :attr:`ssh_login_user`.
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
        
    async def run_command(self, *args, _bg=True, _bg_exc=False, _user=None):
        if _user is None:
            _user = self.runas_user
        return await super().run_command(
            *self.become_privileged_command(_user),
            *args,
            _user=self.ssh_login_user)
        
    async def sshfs_process_factory(self, user):
        if not self.become_privileged(user):
            return await super().sshfs_process_factory(user=user)
        sftp_command_list = self.become_privileged_command(user) + [
            '/bin/sh', '-c',
            f"'for sftp in {' '.join(sftp_server_locations)} ; do test -x $sftp && exec $sftp; done'"]
        sftp_command = " ".join(sftp_command_list)
        return sh.sshfs(
            '-o' 'ssh_command=' + " ".join(
                str(self.ssh).split()[:-1]),
            '-osftp_server='+sftp_command,
            f'{machine.ssh_user_addr(self)}:/',
            self.sshfs_path,
            '-f',
            )

__all__ += ['BecomePrivilegedMixin']
