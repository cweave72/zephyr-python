# Common options for protorpc cli apps.
#
import sys
import functools
import click
import logging

from rich import inspect
from rich.console import Console

from protorpc import build_api

from protorpc.cli import setup_logging, manage_session_state

from protorpc.cli.callsets import (
    load_callset_yaml,
    get_callset_bindings,
    get_callsets,
    refresh_callset_bindings,
    get_registry_table)

from protorpcheader.lib import ProtoRpcHeader, CallsetInfo

logger = logging.getLogger(__name__)

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


def cli_common_opts(func):
    """Decorator defining common options used for cli entrypoints."""

    @click.option("--loglevel", default="info", help="Debug logging level.")
    @click.option(
        "-d", "--debug", is_flag=True, help="Shortcut for --loglevel=debug."
    )
    @click.option("-u", "--udp", is_flag=True, help="Use UDP connection.")
    @click.option("--ip", type=str, help="Device IP address.")
    @click.option("--port", type=int, default=13001, help="RPC server port.")
    @click.option("--hostname", type=str, help="Device hostname.")
    @click.option("-c", "--callsets", type=str, help="Path to callsets.yaml")
    @click.option("--dump-registry", is_flag=True, help="Dumps the callset registry and exits.")
    @click.option("--refresh-bindings", is_flag=True, help="Refreshes callset bindings for the IP.")
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)

    return wrapper


def cli_init(ctx, params):
    """Standard cli entry initializations."""
    # Create the app root logger and setup logging.
    rlogger = logging.getLogger()
    loglevel = "debug" if params.debug else params.loglevel
    setup_logging(rlogger, level=loglevel)

    # Write the params to the click ctx object.
    ctx.obj["cli_params"] = params

    session_changed = manage_session_state()
    logger.debug(f"Session state changed: {session_changed}")

    if params.dump_registry:
        con = Console()
        tbl = get_registry_table()
        con.print(tbl)
        sys.exit(0)

    if session_changed or params.refresh_bindings:
        # If the session has changed, we want to query the server for callset
        # information to refresh stored bindings.
        refresh_callset_bindings(
            protocol="udp" if params.udp else "tcp",
            ip=params.ip,
            port=params.port,
            hostname=params.hostname,
            print_table=True)

        if params.refresh_bindings:
            sys.exit(0)

    if params.callsets is not None:
        logger.debug(f"Using file {params.callsets}.")
        callset_dict = load_callset_yaml(params.callsets)
    else:
        callset_dict = get_callset_bindings(params.ip)
        logger.debug(f"Callset bindings for IP {params.ip}: {callset_dict}")

    if not callset_dict:
        logger.error("No callsets found for the server. Exiting.")
        sys.exit(1)

    try:
        callsets = get_callsets(callset_dict)
        logger.debug(f"callset_dict={callset_dict}")
        logger.debug(f"callsets={callsets}")
    except Exception as e:
        logger.exception(f"{str(e)}")

    try:
        protocol = "udp" if params.udp else "tcp"

        # Build the RPC api and connection object.
        api, conn = build_api(
            ProtoRpcHeader,
            callsets,
            protocol=protocol,
            port=params.port,
            addr=params.ip,
            hostname=params.hostname,
        )
    except Exception as e:
        logger.error(f"RPC api build error: {str(e)}.")
        raise e

    # Return the (api, connection, and callset bindings).
    return api, conn, callset_dict
