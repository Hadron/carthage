# Copyright (C) 2019, 2021, 2022, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

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
