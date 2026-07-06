import logging
import click
from click_repl import repl
from rich.console import Console
from rich.text import Text
from textual.app import App

from subscriber import setup_logging, get_params
from subscriber import client, sysmsg
from subscriber.tui import TuiApp

rlogger = logging.getLogger()
logger = logging.getLogger(__name__)

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option("--host", default="localhost", show_default=True, help="MQTT broker host/IP.")
@click.option("--port", default=1883, show_default=True, type=int, help="MQTT broker port.")
@click.option("--loglevel", default="info", help="Debug logging level.")
@click.option("-d", "--debug", is_flag=True, help="Shortcut for --loglevel=debug.")
@click.pass_context
def cli(ctx, **kwargs):
    """CLI application for client subscriber.
    """
    params = get_params(**kwargs)

    ctx.obj["host"] = params.host
    ctx.obj["port"] = params.port

    if ctx.invoked_subcommand != "tui" and not rlogger.handlers:
        loglevel = "debug" if params.debug else params.loglevel
        setup_logging(rlogger, level=loglevel)


@cli.command
@click.argument("topic")
@click.option("--all", "all_", is_flag=True, help="Subscribe to all publishers (default).")
@click.option("--pub", type=int, default=None, help="Subscribe to a single publisher id.")
@click.pass_context
def sub(ctx, topic, all_, pub):
    """Subscribe to an arbitrary TOPIC and stream best-effort decoded messages
    until Ctrl-C. Every Publisher-module topic is actually published as
    "TOPIC/pubid-<id>"; --pub targets one publisher, --all (the default)
    wildcards all of them.
    """
    try:
        resolved_topic = client.resolve_topic(topic, all_, pub)
    except ValueError as e:
        Console().print(Text(str(e), style="bold red"))
        raise click.exceptions.Exit(1)
    client.SubscriberClient(ctx.obj["host"], ctx.obj["port"], resolved_topic).run()


@cli.command(name="sysmsg")
@click.option("--level", type=click.Choice(sysmsg.LEVEL_CHOICES), default="debug",
              show_default=True, help="Minimum severity level to display.")
@click.option("--all", "_all", is_flag=True, help="Subscribe to all publishers (default).")
@click.option("--pub", type=int, default=None, help="Subscribe to a single publisher id.")
@click.pass_context
def sysmsg_cmd(ctx, level, _all, pub):
    """Subscribe to the "sysmsg" topic and stream decoded system messages,
    filtered on console by --level, until Ctrl-C. Every message received is
    logged to sysmsg.log regardless of level. --pub targets one publisher,
    --all (the default) wildcards all of them.
    """
    try:
        resolved_topic = client.resolve_topic("sysmsg", _all, pub)
    except ValueError as e:
        Console().print(Text(str(e), style="bold red"))
        raise click.exceptions.Exit(1)
    sysmsg.MsgSubscriberClient(
        ctx.obj["host"], ctx.obj["port"], resolved_topic, level=level).run()


@cli.command(name="ls")
def ls():
    """List the available subcommands.
    """
    lines = Text()
    for name, cmd in sorted(cli.commands.items()):
        lines.append(f"{name:<10} ", style="bold cyan")
        lines.append(f"{cmd.get_short_help_str()}\n")
    Console().print(lines)


@cli.command()
def shell():
    """Drops into a command shell.
    """
    prompt_args = {
        "message": "shell > ",
    }
    repl(click.get_current_context(), prompt_kwargs=prompt_args)


@cli.command()
@click.pass_context
def tui(ctx):
    """Launch the TUI.
    """
    app = TuiApp(rlogger, ctx.obj["host"], ctx.obj["port"])
    app.run()


def entrypoint():
    cli(obj={})


if __name__ == "__main__":
    entrypoint()
