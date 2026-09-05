"""click CLI for the Zephyr application generator.

Follows python/subscriber's shape: a click group with `invoke_without_command`,
ctx.obj for shared state, a `tui` subcommand, an `ls` subcommand, and
entrypoint() calling cli(obj={}).

Running `app_gen` bare launches the TUI -- that is the interactive path.
`app_gen new` is fully non-interactive so it stays scriptable.
"""
import logging
import sys

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from app_generator import setup_logging, get_params
from app_generator import boards as boards_mod
from app_generator import generate as gen
from app_generator import modules as modules_mod

rlogger = logging.getLogger()
logger = logging.getLogger(__name__)

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


def _parse_modules(specs):
    """--module NAME[:LEVEL] (repeatable) -> {name: level}."""
    out = {}
    valid = set(modules_mod.SELECTABLE)
    for spec in specs:
        name, _, level = spec.partition(":")
        if name not in valid:
            raise click.BadParameter(
                f"unknown module {name!r}. Choose from: {', '.join(sorted(valid))}")
        level = level or modules_mod.DEFAULT_LOG_LEVEL
        if level not in modules_mod.LOG_LEVELS:
            raise click.BadParameter(
                f"bad log level {level!r} for {name}. "
                f"Choose from: {', '.join(modules_mod.LOG_LEVELS)}")
        out[name] = level
    return out


@click.group(context_settings=CONTEXT_SETTINGS, invoke_without_command=True)
@click.option("--loglevel", default="info", help="Debug logging level.")
@click.option("-d", "--debug", is_flag=True, help="Shortcut for --loglevel=debug.")
@click.pass_context
def cli(ctx, **kwargs):
    """Generate a new Zephyr application from the workspace template."""
    params = get_params(**kwargs)
    ctx.obj["loglevel"] = "debug" if params.debug else params.loglevel

    if ctx.invoked_subcommand not in ("tui", None) and not rlogger.handlers:
        setup_logging(rlogger, level=ctx.obj["loglevel"])

    if ctx.invoked_subcommand is None:
        ctx.invoke(tui)


@cli.command()
@click.pass_context
def tui(ctx):
    """Launch the TUI (default when no subcommand is given)."""
    from app_generator.tui import TuiApp
    TuiApp().run()


@cli.command()
@click.option("--name", required=True, help="Application name (lowercase slug).")
@click.option("--desc", default="A Zephyr application.", help="One-line description.")
@click.option("--net", "net_type", default="wifi",
              type=click.Choice(["none", "wifi", "serial", "eth", "usb"]),
              help="Networking transport.")
@click.option("--ip", "ip_mode", default="dhcp",
              type=click.Choice(["dhcp", "static"]), help="IPv4 addressing.")
@click.option("--addr", default="192.168.1.15", help="Static IPv4 address.")
@click.option("--mask", default="255.255.255.0", help="Static IPv4 netmask.")
@click.option("--gw", default="192.168.1.1", help="Static IPv4 gateway.")
@click.option("--board", "board_list", multiple=True,
              help="Board to support (repeatable).")
@click.option("--module", "module_specs", multiple=True,
              help="Module to enable, NAME[:LEVEL] (repeatable).")
@click.option("--echoserver-transport", default="udp",
              type=click.Choice(["udp", "tcp"]),
              help="Transport for EchoServer, if selected.")
@click.option("--rpc/--no-rpc", default=True, help="Include the ProtoRpc server.")
@click.option("--tracing/--no-tracing", default=False, help="Include CTF tracing.")
@click.option("--nv/--no-nv", default=None, help="Include NVS settings.")
@click.option("--shell/--no-shell", default=False, help="Enable the Zephyr shell.")
@click.option("--led/--no-led", default=True,
              help="Drive the onboard LED via the devicetree led0 alias.")
@click.option("--dest", default=None, help="Destination (default applications/<name>).")
@click.option("--dry-run", is_flag=True, help="Print the manifest; write nothing.")
@click.option("--overwrite", is_flag=True, help="Overwrite an existing app.")
@click.pass_context
def new(ctx, name, desc, net_type, ip_mode, addr, mask, gw, board_list,
        module_specs, echoserver_transport, rpc, tracing, nv, shell, led,
        dest, dry_run, overwrite):
    """Generate a new application non-interactively."""
    console = Console()
    known = {b for b, _, _ in boards_mod.all_boards()}
    for b in board_list:
        if b not in known:
            console.print(Text(
                f"Unknown board {b!r}. Run 'app_gen boards' to list them.",
                style="bold red"))
            raise click.exceptions.Exit(1)
    if not board_list:
        console.print(Text(
            "No --board given; the app will have no boards/ files and "
            "'make appboards' will list nothing.", style="yellow"))

    answers = gen.build_answers(
        app_name=name, description=desc, net_type=net_type, ip_mode=ip_mode,
        ipv4_addr=addr, ipv4_mask=mask, ipv4_gw=gw, use_rpc=rpc,
        use_tracing=tracing, use_nv=nv, use_shell=shell, use_led=led,
        modules=_parse_modules(module_specs),
        echoserver_transport=echoserver_transport, board_list=board_list)

    dest = dest or gen.default_dest(name)
    files = gen.plan_files(answers)

    if dry_run:
        console.print(f"[bold]Would write {len(files)} files to[/bold] {dest}")
        for f in files:
            console.print(f"  {f}")
        added = answers["module_symbols"]["added"]
        if added:
            console.print(f"\n[bold]Pulled in by dependency closure:[/bold] "
                          f"{', '.join(added)}")
        return

    gen.run(answers, dest, overwrite=overwrite)
    console.print(f"[bold green]Generated[/bold green] {dest} ({len(files)} files)")
    console.print("Remember the 'applications' repo is west-managed: be on the "
                  "'main' branch before committing (see the workspace README).")


@cli.command()
@click.argument("path")
def update(path):
    """Re-apply template changes to an app generated earlier."""
    console = Console()
    try:
        gen.update(path)
    except gen.UpdateNotPossible as e:
        console.print(Text(str(e), style="yellow"))
        raise click.exceptions.Exit(1)
    console.print(f"[bold green]Updated[/bold green] {path}")


@cli.command()
def boards():
    """List discoverable boards and their net-type hints."""
    t = Table(title="Boards")
    t.add_column("Board", style="cyan")
    t.add_column("Build target")
    t.add_column("Net hint", style="magenta")
    for name, target, hint in boards_mod.all_boards():
        t.add_row(name, target, hint or "-")
    Console().print(t)


@cli.command(name="modules")
def modules_cmd():
    """List selectable modules and their resolved dependency closure."""
    symmap = modules_mod.symbol_map()
    t = Table(title="Selectable modules")
    t.add_column("Module", style="cyan")
    t.add_column("Symbol")
    t.add_column("Pulls in (closure)", style="magenta")
    for mod in modules_mod.SELECTABLE:
        sym = symmap.get(mod, "?")
        info = gen.resolve_modules({mod: modules_mod.DEFAULT_LOG_LEVEL})
        t.add_row(mod, sym, ", ".join(info["added"]) or "-")
    Console().print(t)


@cli.command(name="ls")
def ls():
    """List the available subcommands."""
    lines = Text()
    for name, cmd in sorted(cli.commands.items()):
        lines.append(f"{name:<10} ", style="bold cyan")
        lines.append(f"{cmd.get_short_help_str()}\n")
    Console().print(lines)


def entrypoint():
    cli(obj={})


if __name__ == "__main__":
    entrypoint()
