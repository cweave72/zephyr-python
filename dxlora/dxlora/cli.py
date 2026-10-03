"""dxlora: command line tool for the DX-LR22-900T22D LoRa module."""
from __future__ import annotations

import functools
import logging
import shlex
import time
from typing import Any, Callable, Optional

import click
import serial

from dxlora.module import DxLoraModule, open_serial
from dxlora.protocol import (CHANNEL_MAX, SETTINGS, ModuleError, Setting,
                             channel_to_mhz, describe_value)

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


class Session:
    """Holds the connection options and the module object of one run.

    The module opens on first use. The shell command shares one session
    between all its commands.
    """

    def __init__(self, port: str, baud: int, parity: str, timeout: float,
                 guard: float) -> None:
        """Stores the options. The port opens later.

        Args:
            port: The serial device path.
            baud: The module UART baud rate.
            parity: "n", "o" or "e".
            timeout: The reply time limit in seconds.
            guard: The quiet time around "+++" in seconds.
        """
        self.port = port
        self.baud = baud
        self.parity = parity
        self.timeout = timeout
        self.guard = guard
        self._module: Optional[DxLoraModule] = None

    @property
    def module(self) -> DxLoraModule:
        """The module object. It opens the port on first access.

        Raises:
            click.ClickException: The port does not open.
        """
        if self._module is None:
            try:
                self._module = DxLoraModule(
                    self.port, self.baud, self.parity, self.timeout,
                    self.guard, serial_factory=open_serial)
            except (serial.SerialException, OSError) as err:
                raise click.ClickException(
                    f"Cannot open {self.port}: {err}") from None
        return self._module

    def close(self) -> None:
        """Leaves AT mode and closes the port, if the port is open."""
        if self._module is not None:
            self._module.close()
            self._module = None


def handle_errors(func: Callable[..., Any]) -> Callable[..., Any]:
    """Turns ModuleError into a click error with the raw reply lines.

    Args:
        func: The command function.

    Returns:
        The wrapped function.
    """
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except ModuleError as err:
            text = str(err)
            if err.lines:
                text += "\n  reply: " + " | ".join(err.lines)
            raise click.ClickException(text) from None
        except serial.SerialException as err:
            raise click.ClickException(f"Serial error: {err}") from None
    return wrapper


def format_setting(setting: Setting, raw: str) -> str:
    """Formats one setting line: name, raw value and a note.

    Args:
        setting: The setting.
        raw: The raw value text.

    Returns:
        The line without line end.
    """
    note = describe_value(setting, raw)
    return f"{setting.name:<8} {raw:<6} {note}".rstrip()


@click.group(context_settings=CONTEXT_SETTINGS)
@click.option("--port", "-p", required=True, metavar="DEVICE",
              help="Serial device, for example /dev/ttyUSB0.")
@click.option("--baud", "-b", default=9600, show_default=True,
              help="Module UART baud rate.")
@click.option("--parity", type=click.Choice(["n", "o", "e"]), default="n",
              show_default=True, help="Module UART parity.")
@click.option("--timeout", default=1.0, show_default=True,
              help="Reply time limit in seconds.")
@click.option("--guard", default=0.0, show_default=True,
              help="Quiet time around +++ in seconds.")
@click.option("--verbose", "-v", is_flag=True, help="Log all serial bytes.")
@click.pass_context
def cli(ctx: click.Context, port: str, baud: int, parity: str,
        timeout: float, guard: float, verbose: bool) -> None:
    """Configure a DX-LR22-900T22D LoRa module over its UART.

    Each command enters AT mode and leaves it at the end. Leaving AT mode
    resets the module. Use the shell command to stay in AT mode.
    """
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    session = Session(port, baud, parity, timeout, guard)
    ctx.obj = session
    ctx.call_on_close(session.close)


def _session(ctx: click.Context) -> Session:
    """Returns the session of a command context.

    Args:
        ctx: The click context.

    Returns:
        The Session object.
    """
    return ctx.obj


@cli.command()
@click.pass_context
@handle_errors
def dump(ctx: click.Context) -> None:
    """Reads and prints all settings."""
    config = _session(ctx).module.read_config()
    if config.version:
        click.echo(f"{'version':<8} {config.version}")
    for name, raw in config.values.items():
        click.echo(format_setting(SETTINGS[name], raw))
    for name, text in config.errors.items():
        click.echo(f"{name:<8} ERROR: {text}")


@cli.command()
@click.argument("name", type=click.Choice(list(SETTINGS)))
@click.pass_context
@handle_errors
def get(ctx: click.Context, name: str) -> None:
    """Reads one setting."""
    raw = _session(ctx).module.query(name)
    click.echo(format_setting(SETTINGS[name], raw))


def _do_set(session: Session, name: str, value: str, reset: bool,
            force: bool) -> None:
    """Sets one setting and prints the outcome.

    Args:
        session: The session.
        name: The setting name.
        value: The value text.
        reset: True to restart the module when the setting needs it.
        force: True to allow a guarded setting.
    """
    result = session.module.set(name, value, reset=reset, force=force)
    if result.value is not None:
        click.echo(format_setting(SETTINGS[name], result.value))
    if result.reset_done:
        click.echo("Module restarted. The change is active.")
    elif result.needs_reset:
        click.echo("Change saved. It is active after the reset command.")


@cli.command("set")
@click.argument("name", type=click.Choice([n for n, s in SETTINGS.items()
                                           if s.writable]))
@click.argument("value")
@click.option("--no-reset", is_flag=True,
              help="Do not restart the module after the change.")
@click.option("--force", is_flag=True,
              help="Allow a setting that can lock out the serial port.")
@click.pass_context
@handle_errors
def set_cmd(ctx: click.Context, name: str, value: str, no_reset: bool,
            force: bool) -> None:
    """Sets one setting. Run "dxlora get --help" for the names."""
    _do_set(_session(ctx), name, value, not no_reset, force)


def _alias(name: str) -> None:
    """Adds a command that reads or sets one setting.

    Args:
        name: The setting name.
    """
    setting = SETTINGS[name]

    @cli.command(name, help=f"Reads or sets {name}. {setting.help}")
    @click.argument("value", required=False)
    @click.option("--no-reset", is_flag=True,
                  help="Do not restart the module after the change.")
    @click.pass_context
    @handle_errors
    def command(ctx: click.Context, value: Optional[str],
                no_reset: bool) -> None:
        session = _session(ctx)
        if value is None:
            click.echo(format_setting(setting, session.module.query(name)))
        else:
            _do_set(session, name, value, not no_reset, False)


for _name in ("channel", "level", "power", "mac"):
    _alias(_name)


@cli.command()
@click.argument("value", type=click.IntRange(0, 65535))
@click.option("--no-reset", is_flag=True,
              help="Do not restart the module after the change.")
@click.pass_context
@handle_errors
def key(ctx: click.Context, value: int, no_reset: bool) -> None:
    """Writes the module key (0-65535). The module cannot report the key."""
    _do_set(_session(ctx), "key", str(value), not no_reset, False)


@cli.command()
@click.pass_context
@handle_errors
def reset(ctx: click.Context) -> None:
    """Restarts the module. Pending changes become active."""
    seen = _session(ctx).module.reset()
    click.echo("Module restarted." if seen else
               "Module restarted. No Power On text was seen.")


@cli.command("factory-reset")
@click.option("--yes", is_flag=True, help="Do not ask for confirmation.")
@click.pass_context
@handle_errors
def factory_reset(ctx: click.Context, yes: bool) -> None:
    """Restores all settings to the factory values (AT+DEFAULT)."""
    if not yes:
        click.confirm("Restore all module settings to the defaults?",
                      abort=True)
    _session(ctx).module.factory_reset()
    click.echo("Factory settings restored. The module UART is 9600 8N1.")


def _hex_int(ctx: click.Context, param: click.Parameter,
             value: Optional[str]) -> Optional[int]:
    """Converts a hex option text to an integer. Used as a click callback.

    Args:
        ctx: The click context.
        param: The option.
        value: The text, for example "41" or "0x41", or None.

    Returns:
        The integer, or None if value is None.

    Raises:
        click.BadParameter: The text is not hex.
    """
    if value is None:
        return None
    try:
        return int(value, 16)
    except ValueError:
        raise click.BadParameter(f"{value!r} is not a hex number.") from None


@cli.command()
@click.option("--text", "-t", help="Send this text.")
@click.option("--hex", "hex_data", help="Send these hex bytes, for example 'aa bb 01'.")
@click.option("--file", "path", type=click.Path(exists=True, dir_okay=False),
              help="Send the content of this file.")
@click.option("--addr", callback=_hex_int, help="Receiver address (hex). Mode 1.")
@click.option("--channel", callback=_hex_int,
              help="Receiver channel (hex). Modes 1 and 2.")
@click.pass_context
@handle_errors
def send(ctx: click.Context, text: Optional[str], hex_data: Optional[str],
         path: Optional[str], addr: Optional[int],
         channel: Optional[int]) -> None:
    """Sends data over the radio. This is the only command that transmits.

    Give one of --text, --hex or --file. The module mode sets the frame:
    mode 0 sends the data as is, mode 1 needs --addr and --channel, and
    mode 2 needs --channel.
    """
    given = [x for x in (text, hex_data, path) if x is not None]
    if len(given) != 1:
        raise click.UsageError("Give exactly one of --text, --hex or --file.")
    if text is not None:
        payload = text.encode()
    elif hex_data is not None:
        try:
            payload = bytes.fromhex(hex_data)
        except ValueError:
            raise click.BadParameter("Not valid hex.",
                                     param_hint="--hex") from None
    else:
        with open(path, "rb") as handle:
            payload = handle.read()
    info = _session(ctx).module.send(payload, addr=addr, channel=channel)
    click.echo(f"Sent {len(payload)} bytes in mode {info.mode} "
               f"({info.frame_len} bytes on the UART, "
               f"about {info.airtime_s:.2f} s on the air).")


@cli.command()
@click.option("--duration", "-d", default=10.0, show_default=True,
              help="Seconds to listen. 0 means until Ctrl-C.")
@click.option("--count", "-n", default=0, help="Stop after this many packets.")
@click.option("--idle", default=0.15, show_default=True,
              help="Quiet time that ends a packet, in seconds.")
@click.option("--hex", "as_hex", is_flag=True, help="Print the data as hex.")
@click.pass_context
@handle_errors
def listen(ctx: click.Context, duration: float, count: int, idle: float,
           as_hex: bool) -> None:
    """Prints received packets. The module leaves AT mode to receive.

    If DRSSI is on, each packet shows its signal strength.
    """
    module = _session(ctx).module
    seen = 0
    click.echo("Listening. Press Ctrl-C to stop.", err=True)
    try:
        for packet in module.receive(duration or None, idle):
            seen += 1
            body = packet.data.hex(" ") if as_hex else repr(
                packet.data.decode("utf-8", errors="replace"))
            rssi = "" if packet.rssi_dbm is None else f"  {packet.rssi_dbm} dBm"
            stamp = time.strftime("%H:%M:%S", time.localtime(packet.timestamp))
            click.echo(f"{stamp}  {len(packet.data):>3} bytes{rssi}  {body}")
            if count and seen >= count:
                break
    except KeyboardInterrupt:
        click.echo()
    click.echo(f"{seen} packet(s) received.", err=True)


@cli.command()
@click.option("--from", "first", default="0", callback=_hex_int,
              show_default=True, help="First channel (hex).")
@click.option("--to", "last", default="63", callback=_hex_int,
              show_default=True, help="Last channel (hex).")
@click.option("--no-restore", is_flag=True,
              help="Do not set the original channel again at the end.")
@click.pass_context
@handle_errors
def scan(ctx: click.Context, first: int, last: int, no_restore: bool) -> None:
    """Measures the noise level of each channel (AT+ERSSI).

    The scan restarts the module once for each channel. It sends no radio data.
    The table lists the channels from quiet to noisy.
    """
    if not 0 <= first <= last <= CHANNEL_MAX:
        raise click.UsageError(f"Use 0 <= --from <= --to <= {CHANNEL_MAX:x}.")
    results = _session(ctx).module.noise_scan(
        list(range(first, last + 1)), restore=not no_restore)
    click.echo("channel  frequency   noise")
    for channel, noise in sorted(results, key=lambda r: r[1]):
        click.echo(f"{channel:02x}       {channel_to_mhz(channel):7.2f} MHz  "
                   f"{noise} dBm")


@cli.command()
@click.pass_context
def shell(ctx: click.Context) -> None:
    """Starts an interactive shell. The module stays in AT mode.

    Type a command such as "get level" or "set channel 41". Type a raw AT
    line such as "AT+HELP", or "+++". Type "exit" to leave. The shell never
    sends other text, because the module would send it over the radio.
    """
    try:
        import readline  # noqa: F401  (adds line editing to input())
    except ImportError:
        pass
    session = _session(ctx)
    commands = click.Group(context_settings=CONTEXT_SETTINGS)
    for name, command in cli.commands.items():
        if name != "shell":
            commands.add_command(command)

    session.module  # Open the port now, so an open error stops the shell.
    click.echo("dxlora shell. Type help, or exit.")
    while True:
        try:
            line = input("dxlora> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo()
            break
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        if line == "help":
            line = "--help"
        try:
            if line == "+++" or line.upper().startswith("AT"):
                for reply in handle_errors(session.module.raw)(line):
                    click.echo(reply)
            else:
                commands.main(args=shlex.split(line), prog_name="",
                              obj=session, standalone_mode=False)
        except click.ClickException as err:
            err.show()
        except click.Abort:
            click.echo("Aborted.")
        except ValueError as err:
            click.echo(f"Error: {err}")


def entrypoint() -> None:
    """Runs the command line tool."""
    cli(prog_name="dxlora")
