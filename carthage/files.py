from .ssh import RsyncPath, SshKey
from tempfile import TemporaryDirectory
from .dependency_injection import *
from . import ConfigLayout, sh


@inject(config = ConfigLayout,
        ssh_key = SshKey)
async def rsync_git_tree(git_tree, target:RsyncPath,
                         *, config, ssh_key):
    '''
    Copy a git tree into a target system.

Clone the ``HEAD`` of a Git working copy into a new temporary directory  This preserves committed files but does not preserve untracked or uncommitted files.  Rsync that directory to the path on a remote system indicated by *target*.
'''

    assert isinstance(target, RsyncPath)
    git_tree = sh.git('rev-parse', '--show-toplevel', _cwd = git_tree)
    git_tree = str(git_tree.stdout, 'utf-8').rstrip()
    dir = None
    try:
        dir = TemporaryDirectory(dir = config.state_dir)
        await sh.git('clone',
                     git_tree, dir.name,
                     _bg = True, _bg_exc = False)
        return await ssh_key.rsync('-a',
                                   dir.name+'/', target)
    finally:
        dir.cleanup()

__all__ = ('rsync_git_tree',)

