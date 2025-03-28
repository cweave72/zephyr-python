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

    logger.debug(f"api={api}")
    system = SystemRpc(api)
    ctx.obj["sys"] = system


@cli.command()
@click.pass_context
def trace_enable(ctx, **kwargs):
    """RPC to enable tracing."""
    # params = get_params(**kwargs)

    system = ctx.obj["sys"]
    try:
        system.enable_trace()
    except Exception as e:
        logger.error(f"{str(e)}")
        return

    state, count = system.get_trace_status()
    logger.info(f"Trace status: state={state}; count={count}")


@cli.command()
@click.option(
    "-o",
    "--out",
    type=str,
    default="trace_dump.dat",
    show_default=True,
    help="File name to write.",
)
@click.pass_context
def dump_trace(ctx, **kwargs):
    """RPC to dump trace ram to file."""
    params = get_params(**kwargs)

    system = ctx.obj["sys"]

    trace_bytes = system.dump_traceram()
    logger.info(f"Read {len(trace_bytes)} trace bytes.")

    if len(trace_bytes) > 0:
        p = Path(params.out)
        p.write_bytes(trace_bytes)
        logger.info(f"Wrote {len(trace_bytes)} to {str(p)}.")


@cli.command()
@click.pass_context
def trace_status(ctx, **kwargs):
    """RPC to get trace status."""
    # params = get_params(**kwargs)

    system = ctx.obj["sys"]
    state, count = system.get_trace_status()
    logger.info(f"Trace status: state={state}; count={count}")


def entrypoint():
    cli(obj={})


if __name__ == "__main__":
    entrypoint()
