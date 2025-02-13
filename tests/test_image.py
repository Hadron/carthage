# Copyright (C) 2018, 2020, 2021, 2024, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

from carthage.pytest import *
import os.path
import pytest
from pathlib import Path
from carthage.dependency_injection import AsyncInjector, InjectionKey, instantiation_not_ready
from carthage import base_injector, sh
from carthage.config import ConfigLayout
from carthage.image import BtrfsVolume, ReflinkVolume, ContainerVolume, ContainerImage, ImageVolume
import posix
import gc

resource_dir = Path(__file__).parent.joinpath('resources')

@pytest.fixture()
def a_injector():
    if posix.geteuid() != 0:
        pytest.skip("Not running as root; volume tests skipped", )
    injector = base_injector.claim()
    cl = injector(ConfigLayout)
    cl.delete_volumes = True
    yield injector(AsyncInjector)
    gc.collect()


@async_test
async def test_btrfs_volume_base(a_injector, loop):
    cl = await a_injector(ConfigLayout)
    try:
        sh.btrfs("filesystem", "df", cl.image_dir, _bg=False)
    except sh.ErrorReturnCode:
        pytest.skip("image_dir not on btrfs volume")
    assert not os.path.exists(os.path.join(cl.image_dir, "foo")), "The volume directory already exists"
    v = await a_injector(ContainerVolume, implementation=BtrfsVolume, name="foo")
    assert isinstance(v.impl, BtrfsVolume)
    assert str(v.path) == os.path.join(cl.image_dir, v.name)
    assert v.name == "foo"
    v.close()


@async_test
async def test_btrfs_clone(a_injector, loop):
    cl = await a_injector(ConfigLayout)
    try:
        sh.btrfs("filesystem", "df", cl.image_dir, _bg=False)
    except sh.ErrorReturnCode:
        pytest.skip("image_dir is not btrfs")
    vol = await a_injector(ContainerVolume, implementation=BtrfsVolume, name="foo2")
    assert isinstance(vol.impl, BtrfsVolume)
    with open(os.path.join(vol.path, "bar.txt"), "w") as f:
        f.write("This is a file\n")
    v2 = await a_injector(ContainerVolume, implementation=BtrfsVolume, name="clone_foo2", clone_from=vol)
    assert os.path.exists(os.path.join(v2.path, "bar.txt"))


@async_test
async def test_reflink_clone(a_injector):
    cl = await a_injector(ConfigLayout)
    path = Path(cl.image_dir)
    assert not (path / "reflink1").exists()
    rl1 = await a_injector(ContainerVolume, implementation=ReflinkVolume,
                           name=path / "reflink1")
    with open(rl1.path / "bar.txt", "wt") as f:
        f.write("This is a test")
    rl2 = await a_injector(ContainerVolume, implementation=ReflinkVolume,
                           name=path / "reflink2", clone_from=rl1)
    assert rl2.path.joinpath("bar.txt").exists()


@async_test
async def test_container_unpack(a_injector, loop):
    try:
        iv = None
        cl = await a_injector(ConfigLayout)
        cl.base_container_image = Path(__file__).parent.joinpath("resources/base_test.tar.gz")
        iv = await a_injector(ContainerImage, "base")
        path = iv.path
        assert os.path.exists(os.path.join(path, "bin/bash"))
    finally:
        del iv


@pytest.mark.no_rootless
@async_test
async def test_image_unpack(loop, a_injector, vm_image):
    print(vm_image.path)
    with vm_image.image_mounted() as mount:
        assert os.path.exists(os.path.join(mount.rootdir, "bin/bash"))

@pytest.mark.parametrize(
    'input,path,format',
    [('foo', 'image_dir/foo.raw', 'raw'),
     ('google.com', 'image_dir/google.com.raw', 'raw'),
     ('google.com.qcow', 'image_dir/google.com.qcow', 'qcow2'),
     ('/google.com.image', '/google.com.image.raw', 'raw'),
     ])
@async_test
async def test_image_volume_find(input, path, format, ainjector):
    ainjector.add_provider(ConfigLayout)
    cl = await ainjector.get_instance_async(ConfigLayout)
    cl.vm_image_dir = 'image_dir'
    with instantiation_not_ready():
        volume = await ainjector(ImageVolume, name=input)
        await volume.find()
        assert str(volume.path) == path
        assert volume.qemu_format == format
        

@async_test
async def test_image_volume_unpack_clone(a_injector):
    ainjector = a_injector
    volume = await ainjector(
        ImageVolume,
        name='some_volume.qcow',
        base_image=resource_dir/'base_test.raw.gz',
        )
    clone_1 = await ainjector(
        ImageVolume, name='clone_1.qcow',
        base_image=volume)
    clone_2 = await ainjector(
        ImageVolume,
        name='clone_2.raw',
        base_image=volume)
    
