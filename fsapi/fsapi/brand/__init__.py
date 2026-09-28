"""fsapi-brand: builds a littlefs image of a directory tree for a FsApi mount.

Branding puts files into the flash file system of a device at build time, for
example /flash/etc/config/net.conf. The tool:

  1. reads <build>/fsapi_layout.json, which the firmware build writes from its
     devicetree (see common/modules/FsApi/scripts/fsapi_layout.py);
  2. builds a littlefs image with the geometry of the mount, from a source
     directory. The directory maps to the mount root:
     <src>/etc/config/net.conf -> /flash/etc/config/net.conf;
  3. writes <out>/brand.bin (the partition content), <out>/brand.hex (the same
     data at the absolute flash address) and <out>/brand.json (addresses);
  4. mounts the image again and checks each file.

The image replaces the whole partition: branding erases the runtime files of
the mount. `make brand` runs this tool and flashes the result.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import click
from intelhex import IntelHex
from littlefs import LittleFS

logger = logging.getLogger(__name__)

# The on-disk format of the device littlefs (v2.9, deps/modules/fs/littlefs).
# littlefs-python bundles a newer littlefs, which can write a newer format. Pin
# the format, or the device cannot mount the image.
LFS_DISK_VERSION = 0x00020001

LAYOUT_FILE = "fsapi_layout.json"


class BrandError(Exception):
    """A branding step failed. The message tells the user what to do."""


def load_layout(build_dir: str | Path,
                mount_point: Optional[str]) -> dict[str, Any]:
    """Reads the layout of one flash mount from <build_dir>/fsapi_layout.json.

    Args:
        build_dir: The application build directory.
        mount_point: The mount point, for example '/flash'. None selects the
            only flash mount of the build.

    Returns:
        The layout entry of the mount (see fsapi_layout.py).

    Raises:
        BrandError: The file does not exist, has no flash mount, or does
            not have mount_point. None with more than one mount is also an
            error.
    """
    path = Path(build_dir) / LAYOUT_FILE
    if not path.is_file():
        raise BrandError(f"{path} does not exist. Build the application first "
                         "(FsApi writes the file during the build).")
    mounts = json.loads(path.read_text())["mounts"]
    if not mounts:
        raise BrandError(f"{path} has no flash mount (no zephyr,fstab,littlefs "
                         "node in the devicetree).")
    if mount_point is None:
        if len(mounts) > 1:
            names = ", ".join(m["mount_point"] for m in mounts)
            raise BrandError(f"The build has more than one flash mount "
                             f"({names}). Select one with --mount.")
        return mounts[0]
    for m in mounts:
        if m["mount_point"] == mount_point:
            return m
    names = ", ".join(m["mount_point"] for m in mounts)
    raise BrandError(f"{mount_point} is not a flash mount of the build. "
                     f"Flash mounts: {names}.")


def source_files(src_dir: str | Path) -> list[tuple[Path, bool]]:
    """Lists the directories and files of a source tree.

    Dot files and dot directories are skipped, for example .gitkeep. A
    directory comes before its content.

    Args:
        src_dir: The root of the source tree.

    Returns:
        A sorted list of (path relative to src_dir, is_dir).
    """
    src = Path(src_dir)
    out = []
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        rel_root = Path(root).relative_to(src)
        for d in dirs:
            out.append((rel_root / d, True))
        for f in sorted(files):
            if f.startswith("."):
                continue
            out.append((rel_root / f, False))
    return out


def make_fs(layout: dict[str, Any], mount: bool = True) -> LittleFS:
    """Makes a littlefs object with the geometry of a mount.

    Args:
        layout: The layout entry of the mount.
        mount: True formats and mounts a new file system. False only
            creates the object, for example to load an image.

    Returns:
        The LittleFS object, with the device on-disk format version.

    Raises:
        BrandError: The partition size is not a multiple of the erase block
            size.
    """
    block_size = layout["erase_block_size"]
    size = layout["partition_size"]
    if size % block_size:
        raise BrandError(f"The partition size {size} is not a multiple of the "
                         f"erase block size {block_size}.")
    return LittleFS(
        block_size=block_size,
        block_count=size // block_size,
        read_size=layout["read_size"],
        prog_size=layout["prog_size"],
        cache_size=layout["cache_size"],
        lookahead_size=layout["lookahead_size"],
        block_cycles=layout["block_cycles"],
        disk_version=LFS_DISK_VERSION,
        mount=mount,
    )


def build_image(layout: dict[str, Any],
                src_dir: str | Path) -> tuple[bytes, list[tuple[str, int]]]:
    """Builds a littlefs image of a source tree.

    Args:
        layout: The layout entry of the mount.
        src_dir: The root of the source tree. It maps to the mount root.

    Returns:
        A tuple (image, entries). The image is the full partition content.
        Each entry is (device path relative to the mount, size in bytes)
        for one file.

    Raises:
        BrandError: A file does not fit into the partition.
    """
    fs = make_fs(layout)
    entries = []
    for rel, is_dir in source_files(src_dir):
        dev_path = "/" + rel.as_posix()
        if is_dir:
            fs.mkdir(dev_path)
            continue
        data = (Path(src_dir) / rel).read_bytes()
        try:
            with fs.open(dev_path, "wb") as f:
                f.write(data)
        except Exception as e:
            raise BrandError(f"Cannot write {dev_path} ({len(data)} B) into the "
                             f"{layout['partition_size']} B partition: {e}") from e
        entries.append((dev_path, len(data)))
    fs.unmount()
    return bytes(fs.context.buffer), entries


def verify_image(layout: dict[str, Any], image: bytes, src_dir: str | Path,
                 entries: list[tuple[str, int]]) -> int:
    """Mounts an image again and compares each file with its source.

    Args:
        layout: The layout entry of the mount.
        image: The image from build_image.
        src_dir: The root of the source tree.
        entries: The entries from build_image.

    Returns:
        The number of used blocks in the image.

    Raises:
        BrandError: A file in the image differs from its source.
    """
    fs = make_fs(layout, mount=False)
    fs.context.buffer[:] = image
    fs.mount()
    for dev_path, _size in entries:
        with fs.open(dev_path, "rb") as f:
            got = f.read()
        want = (Path(src_dir) / dev_path.lstrip("/")).read_bytes()
        if got != want:
            raise BrandError(f"Verification failed: {dev_path} differs.")
    used = fs.used_block_count
    fs.unmount()
    return used


def write_outputs(layout: dict[str, Any], image: bytes,
                  out_dir: str | Path) -> dict[str, Any]:
    """Writes brand.bin, brand.hex, brand.json and partition_offset.

    Args:
        layout: The layout entry of the mount.
        image: The image from build_image.
        out_dir: The output directory. The function creates it if necessary.

    Returns:
        The content of brand.json: mount point, partition offset and size,
        and flash address.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    address = layout["flash_base"] + layout["partition_offset"]

    (out / "brand.bin").write_bytes(image)

    ih = IntelHex()
    ih.frombytes(image, offset=address)
    ih.write_hex_file(str(out / "brand.hex"))

    info = {
        "mount_point": layout["mount_point"],
        "partition_offset": f"0x{layout['partition_offset']:x}",
        "partition_size": layout["partition_size"],
        "flash_address": f"0x{address:x}",
    }
    (out / "brand.json").write_text(json.dumps(info, indent=2) + "\n")
    # One value for each file, for the make recipe.
    (out / "partition_offset").write_text(info["partition_offset"] + "\n")
    return info


@click.command(context_settings=dict(help_option_names=["-h", "--help"]))
@click.option("--build", "build_dir", required=True,
              type=click.Path(exists=True, file_okay=False),
              help="The application build directory.")
@click.option("--src", "src_dir", required=True,
              type=click.Path(exists=True, file_okay=False),
              help="The directory tree to put into the mount.")
@click.option("--mount", "mount_point", default=None,
              help="The flash mount, for example /flash. Required if the "
                   "build has more than one.")
@click.option("--out", "out_dir", default=None,
              help="The output directory (default <build>/brand).")
def cli(build_dir: str, src_dir: str, mount_point: Optional[str],
        out_dir: Optional[str]) -> None:
    """Builds a littlefs brand image of SRC for a FsApi flash mount.
    \f
    Args:
        build_dir: The application build directory.
        src_dir: The source tree.
        mount_point: The flash mount, or None for the only one.
        out_dir: The output directory, or None for <build>/brand.
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    out_dir = out_dir or str(Path(build_dir) / "brand")
    try:
        layout = load_layout(build_dir, mount_point)
        image, entries = build_image(layout, src_dir)
        used = verify_image(layout, image, src_dir, entries)
        info = write_outputs(layout, image, out_dir)
    except BrandError as e:
        click.echo(f"error: {e}", err=True)
        sys.exit(1)

    blocks = layout["partition_size"] // layout["erase_block_size"]
    click.echo(f"Brand image for {layout['mount_point']}: "
               f"{layout['partition_size']} B at {info['flash_address']} "
               f"(partition offset {info['partition_offset']}), "
               f"{used} of {blocks} blocks used.")
    for dev_path, size in entries:
        click.echo(f"  {layout['mount_point']}{dev_path}  ({size} B)")
    click.echo(f"Wrote {out_dir}/brand.bin, brand.hex, brand.json.")


def entrypoint() -> None:
    """Runs the CLI (the fsapi-brand console script)."""
    cli()


if __name__ == "__main__":
    entrypoint()
