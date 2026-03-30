import sys
import atexit
import logging
import click

from rich.console import Console
from rich.panel import Panel

# ProtoRpc modules
from protorpc.cli import get_params
from protorpc.util import ProtoRpcException
from protorpc.cli.common_opts import cli_common_opts, cli_init
from protorpc.cli.common_opts import CONTEXT_SETTINGS

# Callset classes
from rtosutils import RtosUtils


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
    """CLI application for calling RtosUtils RPCs.
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
        RtosUtils.check_version(bindings)
    except ProtoRpcException:
        sys.exit(1)

    ctx.obj['rtosutils'] = RtosUtils(api)
    ctx.obj['conn'] = conn

    connections.append(conn)
    atexit.register(on_exit)


@cli.command
@click.pass_context
def get_tasks(ctx, **kwargs):
    """Prints a table of RTOS tasks from device.
    """
    params = get_params(**kwargs)
    cli_params = ctx.obj['cli_params']

    rtosutils = ctx.obj['rtosutils']
    threads, total_cycles = rtosutils.collect_thread_info()
    tbl = rtosutils.get_system_threads_table(threads, total_cycles)
    con = Console()
    con.print(Panel.fit(tbl))


def entrypoint():
    cli(obj={})


if __name__ == "__main__":
    entrypoint()
