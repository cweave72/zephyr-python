"""fsapi-cli: remote file system access over the FsApiRpc callset."""
from __future__ import annotations

import sys
import atexit
import logging
import tempfile
from pathlib import Path
from typing import Any, Callable, NoReturn, Optional

import click

from rich.console import Console
from rich.markup import escape

# ProtoRpc modules
from protorpc.cli import get_params
from protorpc.util import ProtoRpcException
from protorpc.cli.common_opts import cli_common_opts, cli_init
from protorpc.cli.common_opts import CONTEXT_SETTINGS

# Callset classes
from fsapi import FsApi, FsApiException, EntryType


logger = logging.getLogger(__name__)

connections = []


def on_exit() -> None:
    """Closes all connections at program exit."""
    logger.debug("Closing connections on exit.")
    for con in connections:
        con.close()


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@cli_common_opts
@click.pass_context
def cli(ctx: click.Context, **kwargs: Any) -> None:
    """CLI application for remote file system access over FsApiRpc.
    Paths are absolute device paths, for example /lfs/dir/file.txt.
    \f
    Connects to the device and puts an FsApi object into ctx.obj['fsapi'].
    Exits with status 1 if the connection or the version check fails.

    Args:
        ctx: The click context.
        **kwargs: The common ProtoRpc options (cli_common_opts).
    """
    global connections

    params = get_params(**kwargs)

    try:
        api, conn, bindings = cli_init(ctx, params)
    except Exception as e:
        logger.error(f"Exiting due to error: {str(e)}")
        logger.exception("Exception details:")
        sys.exit(1)

    try:
        FsApi.check_version(bindings)
    except ProtoRpcException:
        sys.exit(1)

    ctx.obj['fsapi'] = FsApi(api)
    ctx.obj['conn'] = conn

    connections.append(conn)
    atexit.register(on_exit)


def run(func: Callable[..., Any], *args: Any) -> Any:
    """Runs an FsApi call. Exits with a message on a file system error.

    Args:
        func: The FsApi method.
        *args: The arguments of func.

    Returns:
        The return value of func.
    """
    try:
        return func(*args)
    except FsApiException as e:
        Console(stderr=True).print(f"[red]error:[/red] {e}")
        sys.exit(1)


@cli.command
@click.argument('path', required=False)
@click.pass_context
def df(ctx: click.Context, path: str | None) -> None:
    """Prints the usage of each file system, or of the one which holds PATH.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        path: A device path, or None for all mounts.
    """
    fs = ctx.obj['fsapi']
    paths = [path] if path else run(fs.mounts)
    for p in paths:
        info = run(fs.info, p)
        total = info.block_size * info.total_blocks
        free = info.block_size * info.free_blocks
        click.echo(f"{info.mount_point}: {info.total_blocks} blocks of "
                   f"{info.block_size} B; size {total} B; "
                   f"used {total - free} B; free {free} B")


@cli.command
@click.pass_context
def mounts(ctx: click.Context) -> None:
    """Lists the mount points.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
    """
    for m in run(ctx.obj['fsapi'].mounts):
        click.echo(m)


@cli.command
@click.argument('path')
@click.pass_context
def ls(ctx: click.Context, path: str) -> None:
    """Lists a directory.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        path: The device directory.
    """
    tbl = run(ctx.obj['fsapi'].ls_table, path)
    Console().print(tbl)


@cli.command
@click.argument('path')
@click.pass_context
def tree(ctx: click.Context, path: str) -> None:
    """Prints a directory and all its subdirectories as a tree.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        path: The device directory.
    """
    root, num_dirs, num_files = run(ctx.obj['fsapi'].tree, path)
    con = Console()
    con.print(root)
    con.print(f"\n{num_dirs} directories, {num_files} files")


@cli.command
@click.argument('path')
@click.pass_context
def stat(ctx: click.Context, path: str) -> None:
    """Prints the type and size of a path.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        path: The device path.
    """
    info = run(ctx.obj['fsapi'].stat, path)
    kind = "dir" if info.type == EntryType.ENTRY_DIR else "file"
    click.echo(f"{path}: {kind}; size {info.size} B")


@cli.command
@click.argument('path')
@click.pass_context
def cat(ctx: click.Context, path: str) -> None:
    """Writes a device file to stdout.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        path: The device file.
    """
    data = run(ctx.obj['fsapi'].get_file, path)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


@cli.command
@click.argument('path')
@click.argument('dest', type=click.Path(dir_okay=False, writable=True))
@click.pass_context
def get(ctx: click.Context, path: str, dest: str) -> None:
    """Copies a device file PATH to the local file DEST.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        path: The device file.
        dest: The local file.
    """
    data = run(ctx.obj['fsapi'].get_file, path)
    with open(dest, 'wb') as f:
        f.write(data)
    click.echo(f"{path} -> {dest}: {len(data)} B")


@cli.command
@click.argument('src', type=click.Path(exists=True, dir_okay=False))
@click.argument('path')
@click.pass_context
def put(ctx: click.Context, src: str, path: str) -> None:
    """Copies the local file SRC to the device file PATH.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        src: The local file.
        path: The device file.
    """
    with open(src, 'rb') as f:
        data = f.read()
    run(ctx.obj['fsapi'].put_file, path, data)
    click.echo(f"{src} -> {path}: {len(data)} B")


@cli.command
@click.argument('path')
@click.pass_context
def rm(ctx: click.Context, path: str) -> None:
    """Removes a file or an empty directory.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        path: The device path.
    """
    run(ctx.obj['fsapi'].rm, path)


@cli.command
@click.argument('src')
@click.argument('dst')
@click.pass_context
def mv(ctx: click.Context, src: str, dst: str) -> None:
    """Renames or moves a file or directory.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        src: The device source path.
        dst: The device destination path.
    """
    run(ctx.obj['fsapi'].mv, src, dst)


@cli.command
@click.argument('path')
@click.pass_context
def mkdir(ctx: click.Context, path: str) -> None:
    """Creates a directory.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        path: The device directory.
    """
    run(ctx.obj['fsapi'].mkdir, path)


@cli.command
@click.pass_context
def closeall(ctx: click.Context) -> None:
    """Closes all file and directory handles on the device.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
    """
    num = run(ctx.obj['fsapi'].close_all)
    click.echo(f"Closed {num} handles.")


@cli.command
@click.argument('mount_point')
@click.option('-y', '--yes', is_flag=True, help="Do not ask for confirmation.")
@click.pass_context
def format(ctx: click.Context, mount_point: str, yes: bool) -> None:
    """Formats the file system at MOUNT_POINT. All its data is lost.
    \f
    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        mount_point: The mount point to erase.
        yes: True skips the confirmation.
    """
    fs = ctx.obj['fsapi']
    known = run(fs.mounts)
    if mount_point not in known:
        Console(stderr=True).print(
            f"[red]error:[/red] {mount_point} is not a mount point. "
            f"Mounts: {', '.join(known)}")
        sys.exit(1)
    if not yes:
        click.confirm(f"Erase all data on {mount_point}?", abort=True)
    run(fs.format, mount_point)
    click.echo(f"Formatted {mount_point}.")


def blob_error(e: Exception) -> NoReturn:
    """Prints a blob error and exits with status 1.

    Args:
        e: The BlobError.
    """
    Console(stderr=True).print(f"[red]error:[/red] {escape(str(e))}",
                               highlight=False)
    sys.exit(1)


PROTO_PATH_HELP = ("A proto directory, after $PROTO_BASE. Repeat for more "
                   "directories, for example the app proto/ directory.")


@cli.command
@click.argument('yaml_file', metavar='YAML',
                type=click.Path(exists=True, dir_okay=False))
@click.argument('path')
@click.option('--proto-path', 'proto_paths', multiple=True,
              type=click.Path(file_okay=False), help=PROTO_PATH_HELP)
@click.pass_context
def pbput(ctx: click.Context, yaml_file: str, path: str,
          proto_paths: tuple[str, ...]) -> None:
    """Writes a .pb.yaml file as a protobuf blob to the device file PATH.
    The replace is atomic: the tool writes PATH.tmp, then renames it.
    \f
    The tool checks the data as fsapi-brand does (nanopb sizes, field and
    enum names). A power loss keeps the old blob or the new blob, because a
    littlefs rename is atomic. The device reads the blob at boot, so reset
    the device to apply it.

    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        yaml_file: The local .pb.yaml file.
        path: The device file, for example /flash/etc/config/net.pb.
        proto_paths: More proto directories after $PROTO_BASE.
    """
    from fsapi.brand import pbblob

    with tempfile.TemporaryDirectory(prefix="pbblob-") as work:
        try:
            lib = pbblob.ProtoLib(pbblob.proto_search_path(proto_paths),
                                  Path(work))
            blob = pbblob.render(Path(yaml_file), lib)
        except pbblob.BlobError as e:
            blob_error(e)

    fs = ctx.obj['fsapi']
    tmp = f"{path}.tmp"
    run(fs.put_file, tmp, blob.data)
    run(fs.mv, tmp, path)
    click.echo(f"{yaml_file} -> {path}: {len(blob.data)} B")


@cli.command
@click.argument('path')
@click.option('--proto', required=True,
              help="The proto file stem, for example NetConf.")
@click.option('--message', required=True,
              help="The message name, for example NetConf.")
@click.option('-o', '--output', type=click.Path(dir_okay=False, writable=True),
              default=None,
              help="Write the YAML to this file, not to stdout.")
@click.option('--proto-path', 'proto_paths', multiple=True,
              type=click.Path(file_okay=False), help=PROTO_PATH_HELP)
@click.pass_context
def pbget(ctx: click.Context, path: str, proto: str, message: str,
          output: Optional[str], proto_paths: tuple[str, ...]) -> None:
    """Reads the protobuf blob PATH and writes it in the .pb.yaml form.
    \f
    The output has all fields, also the fields with the default value. It is
    valid pbput input: get, edit, put.

    Args:
        ctx: The click context. ctx.obj holds the FsApi object.
        path: The device file, for example /flash/etc/config/net.pb.
        proto: The proto file stem.
        message: The message name.
        output: The local output file, or None for stdout.
        proto_paths: More proto directories after $PROTO_BASE.
    """
    from fsapi.brand import pbblob

    data = run(ctx.obj['fsapi'].get_file, path)
    with tempfile.TemporaryDirectory(prefix="pbblob-") as work:
        try:
            lib = pbblob.ProtoLib(pbblob.proto_search_path(proto_paths),
                                  Path(work))
            text = pbblob.decode(data, proto, message, lib, source=path)
        except pbblob.BlobError as e:
            blob_error(e)

    if output is None:
        click.echo(text, nl=False)
        return
    Path(output).write_text(text)
    click.echo(f"{path} -> {output} ({len(data)} B)")


def entrypoint() -> None:
    """Runs the CLI (the fsapi-cli console script)."""
    cli(obj={})


if __name__ == "__main__":
    entrypoint()
