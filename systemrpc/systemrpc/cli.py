import sys
import atexit
import click
from rich import inspect
from pathlib import Path

# ProtoRpc modules
from protorpc.cli import get_params
from protorpc.cli.common_opts import cli_common_opts, cli_init
from protorpc.cli.common_opts import CONTEXT_SETTINGS
from systemrpc import SystemRpc

import logging

logger = logging.getLogger()


connections = []


def on_exit():
    """Cleanup actions on program exit."""
    logger.debug("Closing connections on exit.")
    for con in connections:
        con.close()


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@cli_common_opts
@click.pass_context
def cli(ctx, **kwargs):
    """CLI application for calling System RPCs."""
    global connections

    params = get_params(**kwargs)

    try:
        api, conn = cli_init(ctx, params)
    except Exception as e:
        logger.error(f"Exiting due to error: {str(e)}")
        sys.exit(1)

    connections.append(conn)
    atexit.register(on_exit)

    logger.info(f"api={api}")
    system = SystemRpc(api)
    ctx.obj['sys'] = system


@cli.command()
@click.option("--file", type=str, required=True, help="File name to write.")
@click.pass_context
def dump_trace(ctx, **kwargs):
    """RPC to dump trace ram to file."""
    params = get_params(**kwargs)

    system = ctx.obj["sys"]

    # Get trace address info.
    addr, size = system.get_trace_addr()
    logger.info(f"Trace addr: 0x{addr:08x}; {size} bytes.")

    if size == 0:
        logger.error("Trace buffer size is zero.")
        return

    try:
        bytes_read = system.get_memory(addr, size)
    except Exception as e:
        logger.error(f"{str(e)}")
        return

    p = Path(params.file)
    p.write_bytes(bytes_read)
    logger.info(f"Wrote {len(bytes_read)} to {str(p)}.")


def entrypoint():
    cli(obj={})


if __name__ == "__main__":
    entrypoint()
