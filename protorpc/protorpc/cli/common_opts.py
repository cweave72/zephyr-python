# Common options for protorpc cli apps.
#
import os
import sys
import functools
import click
import logging
from pathlib import Path

from protorpc import build_api
from protorpc.cli import setup_logging
from protorpc.cli.callsets import load_callset_yaml, get_callsets


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
    @click.option("--port", type=int, help="RPC server port.")
    @click.option("--hostname", type=str, help="Device hostname.")
    @click.option("-c", "--callsets", type=str, help="Path to callsets.yaml")
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

    if params.callsets is not None:
        callsets_yaml = params.callsets
    else:
        # Look for ENV[CALLSETS_YAML]
        callsets_yaml = os.environ.get("CALLSET_YAML", None)
        if callsets_yaml is None:
            logger.error(
                "You must either use --callsets or "
                "set env CALLSETS_YAML to point to a valid "
                "callsets.yaml file."
            )
            sys.exit(1)

    if not Path(callsets_yaml).exists():
        logger.error(f"Callsets yaml file {params.callsets} does not exist.")
        sys.exit(1)

    logger.debug(f"Using file {callsets_yaml}.")

    try:
        callset_dict = load_callset_yaml(params.callsets)
        callsets = get_callsets(callset_dict)
        logger.debug(f"callset_dict={callset_dict}")
        logger.debug(f"callsets={callsets}")
    except Exception as e:
        logger.exception(f"{str(e)}")

    try:
        # Import the frame header class local proto lib which should be in the
        # path of the cli app calling this function.
        from protorpcheader.lib import ProtoRpcHeader

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

    return api, conn
