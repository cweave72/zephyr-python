import sys
import atexit
import logging
import click

from rich.console import Console

# ProtoRpc modules
from protorpc.cli import get_params
from protorpc.util import ProtoRpcException
from protorpc.cli.common_opts import cli_common_opts, cli_init
from protorpc.cli.common_opts import CONTEXT_SETTINGS

# Callset classes
from fsapi import FsApi, FsApiException, EntryType


logger = logging.getLogger(__name__)

connections = []


def on_exit():
    """Cleanup actions on program exit.
    """
    logger.debug("Closing connections on exit.")
    for con in connections:
        con.close()


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@cli_common_opts
@click.pass_context
def cli(ctx, **kwargs):
    """CLI application for remote file system access over FsApiRpc.
    Paths are absolute device paths, for example /lfs/dir/file.txt.
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


def run(func, *args):
    """Runs an FsApi call and exits with a message on a file system error.
    """
    try:
        return func(*args)
    except FsApiException as e:
        Console(stderr=True).print(f"[red]error:[/red] {e}")
        sys.exit(1)


@cli.command
@click.pass_context
def df(ctx):
    """Prints the file system usage.
    """
    info = run(ctx.obj['fsapi'].info)
    total = info.block_size * info.total_blocks
    free = info.block_size * info.free_blocks
    click.echo(f"{info.mount_point}: {info.total_blocks} blocks of "
               f"{info.block_size} B; size {total} B; used {total - free} B; "
               f"free {free} B")


@cli.command
@click.argument('path')
@click.pass_context
def ls(ctx, path):
    """Lists a directory.
    """
    tbl = run(ctx.obj['fsapi'].ls_table, path)
    Console().print(tbl)


@cli.command
@click.argument('path')
@click.pass_context
def tree(ctx, path):
    """Prints a directory and all its subdirectories as a tree.
    """
    root, num_dirs, num_files = run(ctx.obj['fsapi'].tree, path)
    con = Console()
    con.print(root)
    con.print(f"\n{num_dirs} directories, {num_files} files")


@cli.command
@click.argument('path')
@click.pass_context
def stat(ctx, path):
    """Prints the type and size of a path.
    """
    info = run(ctx.obj['fsapi'].stat, path)
    kind = "dir" if info.type == EntryType.ENTRY_DIR else "file"
    click.echo(f"{path}: {kind}; size {info.size} B")


@cli.command
@click.argument('path')
@click.pass_context
def cat(ctx, path):
    """Writes a device file to stdout.
    """
    data = run(ctx.obj['fsapi'].get_file, path)
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


@cli.command
@click.argument('path')
@click.argument('dest', type=click.Path(dir_okay=False, writable=True))
@click.pass_context
def get(ctx, path, dest):
    """Copies a device file PATH to the local file DEST.
    """
    data = run(ctx.obj['fsapi'].get_file, path)
    with open(dest, 'wb') as f:
        f.write(data)
    click.echo(f"{path} -> {dest}: {len(data)} B")


@cli.command
@click.argument('src', type=click.Path(exists=True, dir_okay=False))
@click.argument('path')
@click.pass_context
def put(ctx, src, path):
    """Copies the local file SRC to the device file PATH.
    """
    with open(src, 'rb') as f:
        data = f.read()
    run(ctx.obj['fsapi'].put_file, path, data)
    click.echo(f"{src} -> {path}: {len(data)} B")


@cli.command
@click.argument('path')
@click.pass_context
def rm(ctx, path):
    """Removes a file or an empty directory.
    """
    run(ctx.obj['fsapi'].rm, path)


@cli.command
@click.argument('src')
@click.argument('dst')
@click.pass_context
def mv(ctx, src, dst):
    """Renames or moves a file or directory.
    """
    run(ctx.obj['fsapi'].mv, src, dst)


@cli.command
@click.argument('path')
@click.pass_context
def mkdir(ctx, path):
    """Creates a directory.
    """
    run(ctx.obj['fsapi'].mkdir, path)


@cli.command
@click.pass_context
def closeall(ctx):
    """Closes all file and directory handles on the device.
    """
    num = run(ctx.obj['fsapi'].close_all)
    click.echo(f"Closed {num} handles.")


@cli.command
@click.option('-y', '--yes', is_flag=True, help="Do not ask for confirmation.")
@click.pass_context
def format(ctx, yes):
    """Formats the device file system. All data is lost.
    """
    fs = ctx.obj['fsapi']
    if not yes:
        mount_point = run(fs.info).mount_point
        click.confirm(f"Erase all data on {mount_point}?", abort=True)
    run(fs.format)
    click.echo("Formatted.")


def entrypoint():
    cli(obj={})


if __name__ == "__main__":
    entrypoint()
