# Copyright (C) 2019, 2021, 2022, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

import dataclasses
import os.path
from pathlib import Path
from tempfile import TemporaryDirectory
from .dependency_injection import *
from . import ConfigLayout, sh
from .ssh import RsyncPath, SshKey, rsync
from .setup_tasks import *
__all__ = []
rsync_supports_mkpath_state = None


def rsync_supports_mkpath():
    global rsync_supports_mkpath_state
    if rsync_supports_mkpath_state is None:
        rsync_supports_mkpath_state = 'mkpath' in sh.rsync('--help')
    return rsync_supports_mkpath_state


@inject(config=ConfigLayout,
        ainjector=AsyncInjector)
async def rsync_git_tree(git_tree, target: RsyncPath,
                         *, config, ainjector):
    '''
    Copy a git tree into a target system.

Clone the ``HEAD`` of a Git working copy into a new temporary directory  This preserves committed files but does not preserve untracked or uncommitted files.  Rsync that directory to the path on a remote system indicated by *target*.
'''

    assert isinstance(target, RsyncPath)
    rsync_opts = []
    if rsync_supports_mkpath():
        rsync_opts.append('--mkpath')
    if rsync_supports_mkpath() and not str(target.path).endswith('/'):
        target = RsyncPath(target.machine, str(target.path) + '/')
    git_tree = sh.git('rev-parse', '--show-toplevel', _cwd=git_tree)
    git_tree = str(git_tree.stdout, 'utf-8').rstrip()
    dir = None
    try:
        dir = TemporaryDirectory(dir=config.state_dir)
        await sh.git('clone',
                     git_tree, dir.name,
                     _bg=True, _bg_exc=False)
        return await ainjector(rsync, '-a', '--delete',
                               *rsync_opts,
                               dir.name + '/', target)
    finally:
        dir.cleanup()

__all__ += ['rsync_git_tree', ]


def git_tree_hash(git_tree):
    '''
    Return the HEAD of a git tree suitable for use in a setup_task's hash function
'''
    res = sh.git('rev-parse', 'HEAD',
                 _cwd=git_tree,
                 )
    return str(res.stdout, 'utf-8').rstrip()


__all__ += ['git_tree_hash']


def git_checkout_task(url, repo):
    '''Returns a :func:`setup_task` that will checkout a give git repository.
The resulting setup_task has an attribute *repo_path* which is a function returning the path to the repo

'''
    @inject(config=ConfigLayout)
    def repo_path(config):
        checkouts = Path(config.checkout_dir)
        return checkouts / repo

    @setup_task(f"Checkout {repo} repository")
    @inject(injector=Injector)
    async def checkout_repo(self, injector):
        if callable(url):
            url_resolved = injector(url)
        else:
            url_resolved = url
        return await checkout_git_repo(url_resolved, repo, injector=injector)

    @checkout_repo.invalidator()
    @inject(injector=Injector)
    def checkout_repo(self, last_run, injector):
        path = injector(repo_path)
        return path.exists()
    checkout_repo.repo_path = repo_path
    return checkout_repo


@inject(injector=Injector)
def checkout_git_repo(url, repo, *, injector, foreground=False, branch=None):
    '''
    Checkout a git repo.
    :param repo: where to put the repo; the path is not adjusted, so
    if it should be relative to checkout_dir, that's the caller's
    responsibility.
    '''
    if foreground:
        options = dict(_fg=True)
    else: options = dict(_bg=True, _bg_exc=False)
    config = injector(ConfigLayout)
    path = Path(repo)
    os.makedirs(path.parent, exist_ok=True)
    if path.exists():
        if path.is_symlink():
            # do not pull (especially rebase) a symlink into a developer's
            # home directory. Instead simply confirm it is a git repo. 
            return sh.git('status', _cwg=path, **options)
        return sh.git("pull", "--rebase",
                      _cwd=path,
                      **options)
    else:
        branchargs = ['--branch', branch] if branch else []
        return sh.git("clone",
                      url, str(path),
                      *branchargs,
                      **options)


__all__ += ['git_checkout_task', 'checkout_git_repo']


@dataclasses.dataclass
class CdContext:

    '''
    Builds a temporary CD.
    Typical usage::

        iso_builder = CdContext(self.stamp_path 'cidata', '-Vcidata')
        async with iso_builder as cd_info_path:
            # write stuff out into directories under cd_info_path
        # iso_builder.iso_path contains an iso with the information from path
        #genisoimage will be called with -Vcidata per options

    :param path: A path under which to create the cd and temporary directory.
    :param iso_name: The name of the CD to create; the final output is ``path/iso_name``
    :param *options:  any remaining positional arguments are passed as genisoimage options.

    '''
    
    path: Path
    iso_name: str
    options: list[str]

    def __init__(self, path, iso_name, *options):
        self.path = Path(path)
        self.iso_name = iso_name
        self.options = options
        self._iso_path = None
        self.temp = None
        assert '/' not in self.iso_name
        

    @property
    def iso_path(self)-> Path:
        '''If the context has been exited, return the path to the CD.
        Else raise
        '''
        if self._iso_path:
            return self._iso_path
        raise RuntimeError('Run the context before the CD is created.')

    async def __aenter__(self):
        self.path.mkdir(parents=True, exist_ok=True, mode=0o711)
        self.temp = TemporaryDirectory(dir=self.path, prefix='isobuild_', suffix=self.iso_name)
        return Path(self.temp.name)

    async def __aexit__(self, *exc_info):
        if exc_info[0] is None:
            iso_temp = self.path/(self.iso_name+'.tmp')
            try:
                await sh.xorrisofs(
                    '-J', '--rational-rock',
                    '-o', iso_temp,
                    *self.options,
                    self.temp.name,
                    _bg=True, bg_exc=False)
            except sh.ErrorReturnCode as e:
                raise RuntimeError(str(e.stderr, 'utf-8'))

            iso_path = self.path/self.iso_name
            iso_temp.rename(iso_path)
            self._iso_path = iso_path
        else:
            return False

__all__ += ['CdContext']
