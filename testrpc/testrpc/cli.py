import sys
import atexit
import click
from rich import inspect

# ProtoRpc modules
from protorpc.cli import get_params
from protorpc.cli.common_opts import cli_common_opts, cli_init
from protorpc.cli.common_opts import CONTEXT_SETTINGS

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
    """CLI application for calling Test RPCs."""
    global connections

    params = get_params(**kwargs)

    try:
        api, conn = cli_init(ctx, params)
    except Exception as e:
        logger.error(f"Exiting due to error: {str(e)}")
        sys.exit(1)

    connections.append(conn)
    atexit.register(on_exit)

    test = api["TestCallset"]
    ctx.obj["test"] = test


@cli.command()
@click.argument("a")
@click.argument("b")
@click.pass_context
def add(ctx, **kwargs):
    """RPC add call (a + b)"""
    params = get_params(**kwargs)

    test = ctx.obj["test"]
    reply = test.add(a=int(params.a), b=int(params.b))
    logger.info(f"result: sum={reply.result.sum}")


def entrypoint():
    cli(obj={})


if __name__ == "__main__":
    entrypoint()
