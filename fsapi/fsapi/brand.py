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
import json
import logging
import os
import sys
from pathlib import Path

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
    pass


def load_layout(build_dir, mount_point):
    """-> the layout entry of mount_point from <build_dir>/fsapi_layout.json."""
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


def source_files(src_dir):
    """-> sorted list of (relative dir or file path, is_dir). Dot files and
    dot directories are skipped, for example .gitkeep."""
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


def make_fs(layout, mount=True):
    """-> a LittleFS object with the geometry of the layout."""
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


def build_image(layout, src_dir):
    """-> (image bytes, list of (device path, size)) for the source tree."""
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


def verify_image(layout, image, src_dir, entries):
    """Mounts the image again and compares each file with its source."""
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


def write_outputs(layout, image, out_dir):
    """Writes brand.bin, brand.hex and brand.json. -> dict of the addresses."""
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
def cli(build_dir, src_dir, mount_point, out_dir):
    """Builds a littlefs brand image of SRC for a FsApi flash mount."""
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


def entrypoint():
    cli()


if __name__ == "__main__":
    entrypoint()
