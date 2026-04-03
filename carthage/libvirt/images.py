# Copyright (C) 2026, Hadron Industries, Inc.
# Carthage is free software; you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3
# as published by the Free Software Foundation. It is distributed
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the file
# LICENSE for details.

"""
Utilities and filesystem customizations for libvirt image creation.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from pathlib import Path
import contextlib

from carthage import sh
from carthage.config import ConfigLayout
from carthage.dependency_injection import AsyncInjector, inject
from carthage.machine import FilesystemCustomization
from carthage.modeling import ImageRole
from carthage.image import ImageVolume
from carthage.oci import OciEnviron, OciImage
from carthage.setup_tasks import setup_task
from carthage.utils import import_resources_files

__all__ = []

fai_configspace = import_resources_files("carthage") / "resources" / "fai"


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _copy_fai_class_file(path: str, class_name: str, root: Path) -> None:
    source = fai_configspace / "files" / path.lstrip("/") / class_name
    destination = root / path.lstrip("/")
    _ensure_parent(destination)
    shutil.copy2(source, destination)


def ainsl(path: str | Path, pattern: str, line: str | None = None) -> bool:
    """
    Add a line to *path* if no existing line matches *pattern*.

    This mirrors the FAI ``ainsl`` usage in this tree closely enough for
    the libvirt image customizations. If *line* is omitted, a leading ``^``
    is stripped from *pattern* and the remainder is inserted.
    """

    path = Path(path)
    if line is None:
        line = pattern[1:] if pattern.startswith("^") else pattern
    _ensure_parent(path)
    if path.exists():
        contents = path.read_text()
    else:
        contents = ""
    if re.search(pattern, contents, flags=re.MULTILINE):
        return False
    if contents and not contents.endswith("\n"):
        contents += "\n"
    contents += line + "\n"
    path.write_text(contents)
    return True


def _set_shell_assignment(path: Path, key: str, value: str) -> bool:
    path = Path(path)
    _ensure_parent(path)
    assignment = f'{key}="{value}"'
    if path.exists():
        lines = path.read_text().splitlines()
    else:
        lines = []
    changed = False
    for index, current in enumerate(lines):
        if current.startswith(f"{key}="):
            if current != assignment:
                lines[index] = assignment
                changed = True
            break
    else:
        lines.append(assignment)
        changed = True
    if changed:
        path.write_text("\n".join(lines) + "\n")
    return changed


def _clear_shadow_password(path: Path, username: str) -> bool:
    if not path.exists():
        return False
    lines = path.read_text().splitlines()
    changed = False
    for index, current in enumerate(lines):
        if not current.startswith(f"{username}:"):
            continue
        fields = current.split(":")
        if len(fields) > 1 and fields[1] != "":
            fields[1] = ""
            lines[index] = ":".join(fields)
            changed = True
        break
    if changed:
        path.write_text("\n".join(lines) + "\n")
    return changed


def _truncate_if_exists(path: Path) -> bool:
    if not path.exists():
        return False
    path.write_text("")
    return True


class NoRootCustomization(FilesystemCustomization):

    description = "Disable root password and permit su via pam"

    @setup_task("Clear root password")
    def clear_root_password(self):
        _clear_shadow_password(self.path / "etc/shadow", "root")

    @setup_task("Install openroot pam su policy")
    def install_openroot_pam(self):
        _copy_fai_class_file("/etc/pam.d/su", "OPENROOT", self.path)


__all__ += ["NoRootCustomization"]


class SerialCustomization(FilesystemCustomization):

    description = "Enable grub serial console defaults"

    @setup_task("Set grub serial console")
    async def set_grub_terminal(self):
        _set_shell_assignment(
            self.path / "etc/default/grub",
            "GRUB_CMDLINE_LINUX",
            "ro console=tty1 console=ttyS0,115200n81",
        )
        ainsl(self.path / "etc/default/grub", r"^GRUB_TERMINAL=console")


__all__ += ["SerialCustomization"]


class CloudInitCustomization(FilesystemCustomization):

    description = "Apply cloud-init filesystem defaults"

    @setup_task("Install cloud-init packages")
    async def install_cloud_init_packages(self):
        await self.run_command(
            "apt",
            "-y",
            "install",
            "cloud-init",
            "cloud-initramfs-growroot",
            "netplan.io",
        )
        await self.run_command(
            'systemctl',
            'enable',
            'systemd-networkd',)
        

    @setup_task("Install cloud-init config files")
    def install_cloud_init_files(self):
        _copy_fai_class_file("/etc/cloud/ds-identify.cfg", "CLOUD_INIT", self.path)
        _copy_fai_class_file(
            "/etc/cloud/cloud.cfg.d/20_use_netplan.cfg",
            "CLOUD_INIT",
            self.path,
        )

    @setup_task("Remove generated ssh host keys")
    def remove_ssh_host_keys(self):
        ssh_dir = self.path / "etc/ssh"
        if not ssh_dir.exists():
            return
        for key_path in ssh_dir.glob("ssh_host*"):
            if key_path.is_file():
                key_path.unlink()

    @setup_task("Reset machine ids")
    def reset_machine_ids(self):
        _truncate_if_exists(self.path / "etc/machine-id")
        _truncate_if_exists(self.path / "var/lib/dbus/machine-id")

    @setup_task("Set networkd DUID defaults")
    def configure_networkd_duid(self):
        conf_path = (
            self.path
            / "etc/systemd/networkd.conf.d/10-carthage-duid-link-layer.conf"
        )
        _ensure_parent(conf_path)
        conf_path.write_text(
            "[DHCPv4]\n"
            "DUIDType=link-layer\n\n"
            "[DHCPv6]\n"
            "DUIDType=link-layer\n"
        )


__all__ += ["CloudInitCustomization"]


class DebianBaseContainer(ImageRole):

    add_provider(OciEnviron("RESUME=none"))

    class install_packages(FilesystemCustomization):
        description = "Install Debian base image packages"

        @setup_task("Install systemd, grub, and kernel packages")
        async def install_base_packages(self):
            await self.run_command("apt", "update")
            await self.run_command(
                "apt",
                "-y",
                "install",
                "systemd-sysv",
                "grub-efi-amd64",
                "linux-image-amd64",
            )


__all__ += ["DebianBaseContainer"]


def _parse_size_mib(size: str | int) -> int:
    if isinstance(size, int):
        return size
    match = re.fullmatch(r"\s*(\d+)\s*([KMGT]?)B?\s*", size, flags=re.IGNORECASE)
    if not match:
        raise ValueError(f"Unsupported size string: {size!r}")
    value = int(match.group(1))
    suffix = match.group(2).upper()
    multipliers = {
        "": 1,
        "K": 1 / 1024,
        "M": 1,
        "G": 1024,
        "T": 1024 * 1024,
    }
    mib = value * multipliers[suffix]
    return int(mib)


def create_efi_disk(path: str, size_mib: int):
    """
    Create a raw disk image with a 600 MiB EFI partition and the remaining
    space allocated to a Linux native filesystem (btrfs).
    """

    import guestfs

    os.makedirs(os.path.dirname(path), exist_ok=True)

    total_bytes = size_mib * 1024 * 1024
    with open(path, "wb") as f:
        f.truncate(total_bytes)

    total_sectors = size_mib * 1024 * 1024 // 512
    g = guestfs.GuestFS(python_return_dict=True)
    g.add_drive_opts(path, format="raw", readonly=0)
    g.launch()

    dev = "/dev/sda"
    g.part_init(dev, "gpt")

    efi_start = 2048
    efi_sectors = (600 * 1024 * 1024) // 512 - 34
    efi_end = efi_start + efi_sectors - 1
    second_part_end = total_sectors - 34

    g.part_add(dev, "p", efi_start, efi_end)
    g.part_set_gpt_type(dev, 1, "c12a7328-f81f-11d2-ba4b-00a0c93ec93b")
    g.mkfs("vfat", f"{dev}1")

    g.part_add(dev, "p", efi_end + 1, second_part_end)
    g.part_set_gpt_type(dev, 2, "0fc63daf-8483-4772-8e79-3d69d8477de4")
    g.mkfs("btrfs", f"{dev}2")

    g.shutdown()
    g.close()

    return path


def _guestfs_handle(image_path: str | Path):
    import guestfs

    g = guestfs.GuestFS(python_return_dict=True)
    g.add_drive_opts(str(image_path), format="raw", readonly=0)
    g.launch()
    return g


def _tar_compression(tar_archive: str | Path) -> str | None:
    suffixes = Path(tar_archive).suffixes
    if suffixes[-2:] == [".tar", ".gz"] or suffixes[-1:] == [".tgz"]:
        return "gzip"
    return None


def guestfs_image_from_tar(image_path: str | Path, tar_archive: str | Path):
    g = _guestfs_handle(image_path)
    try:
        g.mount("/dev/sda2", "/")
        g.mkdir_p("/boot")
        g.mkdir_p("/boot/efi")
        g.mount("/dev/sda1", "/boot/efi")
        compress = _tar_compression(tar_archive)
        if compress:
            g.tar_in(str(tar_archive), "/", compress=compress)
        else:
            g.tar_in(str(tar_archive), "/")
        g.sync()
    finally:
        with contextlib.suppress(Exception):
            g.umount_all()
        g.shutdown()
        g.close()
    return image_path


def guestfs_make_bootable(image_path: str | Path):
    g = _guestfs_handle(image_path)
    try:
        g.mount("/dev/sda2", "/")
        g.mkdir_p("/boot")
        g.mkdir_p("/boot/efi")
        g.mount("/dev/sda1", "/boot/efi")
        root_uuid = g.vfs_uuid("/dev/sda2")
        efi_uuid = g.vfs_uuid("/dev/sda1")
        g.write(
            "/etc/fstab",
            (
                f"UUID={root_uuid} / btrfs defaults 0 1\n"
                f"UUID={efi_uuid} /boot/efi vfat umask=0077 0 1\n"
            ),
        )
        g.command(
            [
                "/usr/sbin/grub-install",
                "--target=x86_64-efi",
                "--efi-directory=/boot/efi",
                "--no-nvram",
                "--removable",
                "/dev/sda",
            ]
        )
        g.command(["/usr/sbin/update-grub"])
        g.sync()
    finally:
        with contextlib.suppress(Exception):
            g.umount_all()
        g.shutdown()
        g.close()
    return image_path


@inject(ainjector=AsyncInjector, config=ConfigLayout)
async def guestfs_container_to_vm(
    volume: OciImage,
    output: str,
    size: str | int,
    *,
    config,
    image_volume_class=ImageVolume,
    ainjector,
):
    async def populate_callback(image_volume):
        nonlocal volume
        if image_volume.path != output_path:
            raise RuntimeError(
                f"ImageVolume path {image_volume.path} does not match requested output {output_path}"
            )
        if isinstance(volume, type):
            volume = await ainjector(volume)
        await volume.async_become_ready()
        if output_path.exists():
            output_path.unlink()
        os.makedirs(output_path.parent, exist_ok=True)
        with tempfile.TemporaryDirectory(
            dir=output_path.parent,
            prefix="guestfs-container-to-vm-",
        ) as tmp_d:
            tmp = Path(tmp_d).absolute()
            async with contextlib.AsyncExitStack() as stack:
                if isinstance(volume, OciImage):
                    path = await stack.enter_async_context(volume.filesystem_access())
                else:
                    path = volume.path
                tar_path = tmp / "base.tar.gz"
                await sh.tar(
                    "-C",
                    str(path),
                    "--xattrs",
                    "--xattrs-include=*.*",
                    "-czf",
                    str(tar_path),
                    ".",
                    _bg=True,
                    _bg_exc=False,
                )
            image_path = tmp / "image.raw"
            create_efi_disk(str(image_path), _parse_size_mib(size))
            guestfs_image_from_tar(image_path, tar_path)
            guestfs_make_bootable(image_path)
            os.rename(image_path, output_path)

    output_path = Path(output)
    if not output_path.is_absolute():
        output_path = Path(config.vm_image_dir).joinpath(output)
    return await ainjector(
        image_volume_class,
        name=output_path.absolute(),
        populate=populate_callback,
        size=1,
    )


__all__ += [
    "ainsl",
    "create_efi_disk",
    "fai_configspace",
    "guestfs_container_to_vm",
    "guestfs_image_from_tar",
    "guestfs_make_bootable",
]
