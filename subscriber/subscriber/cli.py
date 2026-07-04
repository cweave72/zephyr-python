import sys
import atexit
import logging
import click

from subscriber import setup_logging, get_params
from subscriber.shell import SubscriberShell


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option("--loglevel", default="info", help="Debug logging level.")
@click.option( "-d", "--debug", is_flag=True, help="Shortcut for --loglevel=debug.")
@click.pass_context
def cli(ctx, **kwargs):
    """CLI application for client subscriber.
    """
    params = get_params(**kwargs)

    rlogger = logging.getLogger()
    loglevel = "debug" if params.debug else params.loglevel
    setup_logging(rlogger, level=loglevel)


@cli.command
@click.option("--host", required=True, help="MQTT broker host/IP.")
@click.option("--port", default=1883, show_default=True, type=int, help="MQTT broker port.")
@click.pass_context
def shell(ctx, **kwargs):
    """Connects to the MQTT broker and opens a subscriber shell.
    """
    params = get_params(**kwargs)
    SubscriberShell(params.host, params.port).cmdloop()


def entrypoint():
    cli(obj={})


if __name__ == "__main__":
    entrypoint()
