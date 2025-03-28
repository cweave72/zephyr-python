import logging
from rich.logging import RichHandler
from rich.console import Console
from rich import inspect

import click

from trace_tool.ctf_parser import TraceParser
from trace_tool.ext_events import tracemodule_event

logger = logging.getLogger()

loglevels = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

fmt_str = "[%(levelname)6s] (%(filename)s:%(lineno)s) %(message)s"


def setup_logging(rootlogger, level, logfile=None):

    rootlogger.setLevel(logging.DEBUG)

    if logfile:
        fh = logging.FileHandler(logfile, mode='w')
        fmt = logging.Formatter(fmt=fmt_str)

        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)
        rootlogger.addHandler(fh)

    con = Console()
    if con.is_terminal:
        ch = RichHandler(rich_tracebacks=True, show_time=False)
    else:
        ch = logging.StreamHandler()
        fmt = logging.Formatter(fmt=fmt_str)
        ch.setFormatter(fmt)

    ch.setLevel(loglevels[level])
    rootlogger.addHandler(ch)


CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])

def get_params(**kwargs):
    """Converts kwargs to Params class.
    """

    class Params:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    return Params(**kwargs)


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option("--loglevel", default="info", help="Debug logging level.")
@click.option(
    "-d", "--debug", is_flag=True, help="Shortcut for --loglevel=debug."
)
@click.pass_context
def cli(ctx, **kwargs):
    """CLI application for Displaying trace info."""
    global connections

    params = get_params(**kwargs)

    if params.debug:
        params.loglevel = 'debug'

    setup_logging(logger, params.loglevel)



@cli.command()
@click.argument("file")
@click.option("-p", "--pager", is_flag=True, help="Display using a pager.")
@click.pass_context
def table(ctx, **kwargs):
    """Display trace table. """
    params = get_params(**kwargs)

    parser = TraceParser(params.file, ext_events=tracemodule_event)
    table = parser.build_table()
    con = Console()
    if params.pager:
        with con.pager(styles=True):
            con.print(table)
    else:
        con.print(table)


def entrypoint():
    cli(obj={})


if __name__ == "__main__":
    entrypoint()
