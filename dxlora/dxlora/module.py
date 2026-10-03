"""Serial driver for the DX-LR22-900T22D module.

DxLoraModule sends AT commands and parses the replies. It never sends user
data over the radio. In transparent mode, every byte on the UART goes on the
air. Thus the class sends "+++" first and sends other text only in AT mode.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Optional

import serial

from dxlora.protocol import (BAUD_RATES, PACKET_SIZES, SETTINGS, ModuleError,
                             Setting, airtime_s, decode_rssi, encode_value,
                             frame_broadcast, frame_fixed, is_error_line,
                             parse_help, parse_response, raise_on_error)

logger = logging.getLogger(__name__)

# Read poll interval of the serial port in seconds.
POLL_S = 0.05
# Time to wait for the "Power On" text after a reset in seconds.
RESET_WAIT_S = 3.0
# Time the module needs after "Power on" before it accepts data, in seconds.
START_DELAY_S = 0.1
# Extra time after the estimated air time of a packet, in seconds.
TX_MARGIN_S = 0.3

_PARITY = {
    "n": serial.PARITY_NONE,
    "o": serial.PARITY_ODD,
    "e": serial.PARITY_EVEN,
}
_PARITY_BY_CODE = {0: "n", 1: "o", 2: "e"}
_ENTRY_RE = re.compile(r"ent(?:ry|er)\s*at", re.IGNORECASE)
_EXIT_RE = re.compile(r"exit\s*at", re.IGNORECASE)
_POWER_ON_RE = re.compile(r"power\s*on", re.IGNORECASE)
_VALUE_LINE_RE = re.compile(r"^\+\s*[A-Za-z]+\s*=")


def open_serial(port: str, baud: int, parity: str) -> Any:
    """Opens a serial port with 8 data bits and 1 stop bit.

    Args:
        port: The device path, for example /dev/ttyUSB0.
        baud: The baud rate.
        parity: "n" (none), "o" (odd) or "e" (even).

    Returns:
        The open pyserial port.
    """
    return serial.Serial(port, baudrate=baud, bytesize=serial.EIGHTBITS,
                         parity=_PARITY[parity], stopbits=serial.STOPBITS_ONE,
                         timeout=POLL_S, write_timeout=1.0)


@dataclass
class SetResult:
    """The outcome of a set command.

    Attributes:
        value: The value the module echoed, or None.
        needs_reset: True if the setting needs AT+RESET to take effect.
        reset_done: True if the driver sent AT+RESET.
    """

    value: Optional[str]
    needs_reset: bool
    reset_done: bool


@dataclass
class ModuleConfig:
    """A snapshot of the module settings, as numbers.

    Attributes:
        values: Raw value text of each readable setting, by setting name.
        errors: Error text of each setting that could not be read.
        version: The firmware version from AT+HELP, or None.
    """

    values: dict[str, str]
    errors: dict[str, str]
    version: Optional[str] = None

    def number(self, name: str) -> int:
        """Returns a setting as an integer.

        Args:
            name: The setting name. Channel values are hex.

        Returns:
            The value as an integer.

        Raises:
            KeyError: The setting was not read.
        """
        raw = self.values[name]
        return int(raw, 16) if name == "channel" else int(raw)


@dataclass
class SendInfo:
    """The outcome of a send.

    Attributes:
        mode: The transmission mode of the module (0, 1 or 2).
        frame_len: The number of bytes written to the UART, with framing.
        airtime_s: The estimated time on the air in seconds.
    """

    mode: int
    frame_len: int
    airtime_s: float


@dataclass
class Packet:
    """One received packet.

    Attributes:
        data: The payload without the RSSI byte.
        rssi_dbm: The signal strength in dBm, or None if DRSSI is off.
        timestamp: The time of the last byte, from time.time().
    """

    data: bytes
    rssi_dbm: Optional[int]
    timestamp: float


class DxLoraModule:
    """AT command interface to one module.

    This is the class to use from other applications. Example:

        with DxLoraModule("/dev/ttyUSB0") as mod:
            mod.set_channel(0x41)
            print(mod.get_level())

    Use the class as a context manager. Leaving the context exits AT mode.
    The exit resets the module, and the module returns to transparent mode.
    """

    def __init__(self, port: str, baud: int = 9600, parity: str = "n",
                 timeout: float = 1.0, guard: float = 0.0,
                 serial_factory: Callable[[str, int, str], Any] = open_serial
                 ) -> None:
        """Opens the serial port. The module state is not changed.

        Args:
            port: The device path, for example /dev/ttyUSB0.
            baud: The baud rate of the module UART.
            parity: "n" (none), "o" (odd) or "e" (even).
            timeout: The time to wait for a reply in seconds.
            guard: The quiet time before and after "+++" in seconds.
            serial_factory: Opens the port. Tests replace it with a fake.
        """
        self.timeout = timeout
        self.guard = guard
        self._ser = serial_factory(port, baud, parity)
        self._in_at = False
        self._pending_baud: Optional[int] = None
        self._pending_parity: Optional[str] = None
        self._reset_pending = False

    def __enter__(self) -> "DxLoraModule":
        """Returns the module for use in a with block.

        Returns:
            This object.
        """
        return self

    def __exit__(self, *exc: object) -> None:
        """Leaves AT mode and closes the port."""
        self.close()

    @property
    def in_at_mode(self) -> bool:
        """True if the driver knows the module is in AT mode."""
        return self._in_at

    @property
    def reset_pending(self) -> bool:
        """True if a set command is waiting for AT+RESET."""
        return self._reset_pending

    def close(self, exit_at: bool = True) -> None:
        """Closes the port.

        Args:
            exit_at: If True and the module is in AT mode, send "+++" first.
                The module resets and returns to transparent mode.
        """
        try:
            if exit_at and self._in_at:
                self.exit_at()
        except ModuleError as err:
            logger.warning("Could not leave AT mode: %s", err)
        finally:
            self._ser.close()

    # -- low level I/O ------------------------------------------------------

    def _write(self, data: bytes) -> None:
        """Clears the input buffer and writes bytes to the port.

        Args:
            data: The bytes to send.
        """
        self._ser.reset_input_buffer()
        logger.debug("TX %r", data)
        self._ser.write(data)

    def _read_until(self, done: Callable[[list[str]], bool],
                    timeout: Optional[float] = None,
                    idle: Optional[float] = None) -> list[str]:
        """Reads lines until a condition is true or time runs out.

        Args:
            done: Called with all lines so far. Returns True to stop.
            timeout: The maximum time in seconds. Default is self.timeout.
            idle: If set, stop when no byte arrives for this many seconds
                after the first line.

        Returns:
            The lines received without line ends. A partial last line is
            included.
        """
        limit = time.monotonic() + (self.timeout if timeout is None else timeout)
        last_rx = time.monotonic()
        buf = b""
        lines: list[str] = []
        while time.monotonic() < limit:
            chunk = self._ser.read(self._ser.in_waiting or 1)
            if not chunk:
                if idle is not None and lines and \
                        time.monotonic() - last_rx > idle:
                    break
                time.sleep(0.002)
                continue
            last_rx = time.monotonic()
            logger.debug("RX %r", chunk)
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                text = raw.decode("ascii", errors="replace").strip()
                if text:
                    lines.append(text)
            if done(lines):
                return lines
        tail = buf.decode("ascii", errors="replace").strip()
        if tail:
            lines.append(tail)
        return lines

    def _wait_power_on(self, seen: list[str]) -> bool:
        """Waits for the "Power On" text after a restart.

        Args:
            seen: Lines already received. They may hold the text.

        Returns:
            True if the module sent "Power On".
        """
        if any(_POWER_ON_RE.search(x) for x in seen):
            return True
        lines = self._read_until(
            lambda ls: any(_POWER_ON_RE.search(x) for x in ls), RESET_WAIT_S)
        return any(_POWER_ON_RE.search(x) for x in lines)

    # -- AT mode ------------------------------------------------------------

    def enter_at(self) -> None:
        """Enters AT mode with "+++".

        The "+++" text switches the mode. If the module is already in AT mode,
        the module leaves it, and this method sends "+++" again.

        Raises:
            ModuleError: The module does not answer, or does not confirm AT mode.
        """
        if self._in_at:
            return
        lines: list[str] = []
        for _ in range(2):
            lines = self._toggle()
            text = " ".join(lines)
            if _EXIT_RE.search(text):
                self._wait_power_on(lines)
                continue
            if _ENTRY_RE.search(text):
                break
        else:
            raise ModuleError("The module did not confirm AT mode.", lines=lines)
        self._in_at = True
        # Verify with "AT". This is safe: the module is in AT mode.
        resp = self._exchange("AT", expect_ok=True)
        if not resp.ok:
            self._in_at = False
            raise ModuleError("No OK reply to AT after +++.", lines=resp.lines)

    def exit_at(self) -> None:
        """Leaves AT mode with "+++". The module resets.

        Raises:
            ModuleError: The module does not confirm the exit.
        """
        if not self._in_at:
            return
        lines = self._toggle()
        self._in_at = False
        if not any(_EXIT_RE.search(x) or _POWER_ON_RE.search(x) for x in lines):
            raise ModuleError("The module did not confirm the AT mode exit.",
                              lines=lines)
        self._wait_power_on(lines)

    def _toggle(self) -> list[str]:
        """Sends "+++" with a line end and reads the reply.

        The module ignores a bare "+++". It needs the CR LF line end.

        Returns:
            The reply lines.
        """
        time.sleep(self.guard)
        self._write(b"+++\r\n")
        time.sleep(self.guard)
        return self._read_until(
            lambda ls: any(_ENTRY_RE.search(x) or _EXIT_RE.search(x)
                           for x in ls))

    # -- commands -----------------------------------------------------------

    def _exchange(self, command: str, expect_ok: bool,
                  timeout: Optional[float] = None):
        """Sends one AT line and parses the reply. The module must be in AT mode.

        Args:
            command: The command text without the line end.
            expect_ok: True for a set command, which ends with "OK".
                False for a query, which ends with a "+KEY=value" line.
            timeout: The reply time limit in seconds.

        Returns:
            The parsed response.

        Raises:
            ModuleError: The module returns an error code, or no reply.
        """
        def done(ls: list[str]) -> bool:
            if any(is_error_line(x) for x in ls):
                return True
            if any(x.upper() == "OK" for x in ls):
                return True
            return not expect_ok and any(_VALUE_LINE_RE.match(x) for x in ls)

        self._write(command.encode("ascii") + b"\r\n")
        lines = self._read_until(done, timeout)
        resp = parse_response(lines)
        raise_on_error(resp)
        if not lines:
            raise ModuleError(f"No reply to {command}.")
        if expect_ok and not resp.ok:
            raise ModuleError(f"No OK reply to {command}.", lines=lines)
        return resp

    def command(self, command: str, expect_ok: bool = True):
        """Sends one AT command. Enters AT mode first if needed.

        Args:
            command: The command text, for example "AT+LEVEL2".
            expect_ok: See _exchange.

        Returns:
            The parsed response.

        Raises:
            ModuleError: The module returns an error code, or no reply.
        """
        self.enter_at()
        return self._exchange(command, expect_ok)

    def _setting(self, name: str) -> Setting:
        """Finds a setting by name.

        Args:
            name: The setting name, case insensitive.

        Returns:
            The setting.

        Raises:
            ModuleError: The name is not known.
        """
        try:
            return SETTINGS[name.lower()]
        except KeyError:
            raise ModuleError(f"Unknown setting {name!r}. "
                              f"Known: {', '.join(SETTINGS)}.") from None

    def query(self, name: str) -> str:
        """Reads one setting.

        Args:
            name: The setting name, for example "level".

        Returns:
            The raw value text from the module, for example "2".

        Raises:
            ModuleError: The setting cannot be read, or the module fails.
        """
        setting = self._setting(name)
        if not setting.readable:
            raise ModuleError(f"{setting.name} cannot be read.")
        resp = self.command(f"AT+{setting.cmd}", expect_ok=False)
        if resp.key != setting.cmd or resp.value is None:
            raise ModuleError(f"Unexpected reply to AT+{setting.cmd}.",
                              lines=resp.lines)
        return resp.value

    def set(self, name: str, value: str, reset: bool = True,
            force: bool = False) -> SetResult:
        """Writes one setting.

        Args:
            name: The setting name, for example "level".
            value: The value as text, for example "3".
            reset: If True, send AT+RESET when the setting needs it.
            force: Allow a setting that has a guard warning.

        Returns:
            The result of the set.

        Raises:
            ModuleError: The value or setting is not valid, the setting has a
                guard and force is False, or the module fails.
        """
        setting = self._setting(name)
        if not setting.writable:
            raise ModuleError(f"{setting.name} cannot be set.")
        if setting.guard and not force:
            raise ModuleError(f"{setting.guard} Use --force to set it.")
        try:
            arg = encode_value(setting, value)
        except ValueError as err:
            raise ModuleError(str(err)) from None
        resp = self.command(f"AT+{setting.cmd}{arg}", expect_ok=True)
        if setting.name == "baud":
            self._pending_baud = BAUD_RATES[int(arg)]
        elif setting.name == "parity":
            self._pending_parity = _PARITY_BY_CODE[int(arg)]
        reset_done = False
        if setting.reset:
            if reset:
                self.reset()
                reset_done = True
            else:
                self._reset_pending = True
        return SetResult(resp.value, setting.reset, reset_done)

    # -- typed accessors ------------------------------------------------------

    def read_config(self) -> ModuleConfig:
        """Reads every readable setting and the firmware version.

        A setting that fails is stored in the errors field. The method does
        not raise for a single failed setting.

        Returns:
            The settings snapshot.
        """
        self.enter_at()  # Fail at once if the module does not answer.
        values: dict[str, str] = {}
        errors: dict[str, str] = {}
        for setting in SETTINGS.values():
            if not (setting.readable and setting.in_dump):
                continue
            try:
                values[setting.name] = self.query(setting.name)
            except ModuleError as err:
                errors[setting.name] = str(err)
        version: Optional[str] = None
        try:
            version = self.help().get("VERSION")
        except ModuleError as err:
            errors["version"] = str(err)
        return ModuleConfig(values, errors, version)

    def get_channel(self) -> int:
        """Reads the channel.

        Returns:
            The channel number, 0 to 0x63.
        """
        return int(self.query("channel"), 16)

    def set_channel(self, channel: int, reset: bool = True) -> SetResult:
        """Sets the channel.

        Args:
            channel: The channel number, 0 to 0x63.
            reset: If True, restart the module so the change applies.

        Returns:
            The result of the set.
        """
        return self.set("channel", f"{channel:x}", reset)

    def get_level(self) -> int:
        """Reads the air rate level.

        Returns:
            The level, 0 to 7.
        """
        return int(self.query("level"))

    def set_level(self, level: int, reset: bool = True) -> SetResult:
        """Sets the air rate level.

        Args:
            level: The level, 0 to 7.
            reset: If True, restart the module so the change applies.

        Returns:
            The result of the set.
        """
        return self.set("level", str(level), reset)

    def get_power(self) -> int:
        """Reads the transmit power.

        Returns:
            The power in dBm.
        """
        return int(self.query("power"))

    def set_power(self, dbm: int, reset: bool = True) -> SetResult:
        """Sets the transmit power.

        Args:
            dbm: The power in dBm, 0 to 22.
            reset: If True, restart the module so the change applies.

        Returns:
            The result of the set.
        """
        return self.set("power", str(dbm), reset)

    def get_mac(self) -> int:
        """Reads the device address.

        Returns:
            The address as a 16-bit number.
        """
        return int(self.query("mac").replace(",", ""), 16)

    def set_mac(self, address: int, reset: bool = True) -> SetResult:
        """Sets the device address.

        Args:
            address: The address as a 16-bit number.
            reset: If True, restart the module so the change applies.

        Returns:
            The result of the set.
        """
        return self.set("mac", f"{address:04x}", reset)

    # -- radio data -----------------------------------------------------------

    def send(self, payload: bytes, addr: Optional[int] = None,
             channel: Optional[int] = None, wait: bool = True) -> SendInfo:
        """Sends data over the radio.

        The method reads MODE, PACKET and LEVEL in AT mode. It builds the frame
        for the mode. Then it leaves AT mode and writes the frame.

        Mode 0 (transparent) sends the payload as is. Mode 1 (fixed-point)
        needs addr and channel of the receiver. Mode 2 (broadcast) needs the
        channel of the receiver.

        Args:
            payload: The data to send.
            addr: The receiver address for mode 1.
            channel: The receiver channel for modes 1 and 2.
            wait: If True, wait for the estimated end of the transmission.

        Returns:
            The send information.

        Raises:
            ModuleError: The payload is empty or too long, the addr or channel
                does not match the mode, or the module fails.
        """
        if not payload:
            raise ModuleError("The payload is empty.")
        mode = int(self.query("mode"))
        limit = PACKET_SIZES[int(self.query("packet"))]
        level = int(self.query("level"))
        if len(payload) > limit:
            raise ModuleError(f"The payload has {len(payload)} bytes. The "
                              f"packet length is {limit} bytes.")
        try:
            if mode == 0:
                if addr is not None or channel is not None:
                    raise ModuleError("addr and channel apply to modes 1 and "
                                      "2. The module is in mode 0.")
                frame = payload
            elif mode == 1:
                if addr is None or channel is None:
                    raise ModuleError("Mode 1 needs addr and channel.")
                frame = frame_fixed(addr, channel, payload)
            else:
                if channel is None or addr is not None:
                    raise ModuleError("Mode 2 needs channel, and no addr.")
                frame = frame_broadcast(channel, payload)
        except ValueError as err:
            raise ModuleError(str(err)) from None
        self.exit_at()
        time.sleep(START_DELAY_S)
        self._write(frame)
        self._ser.flush()
        air = airtime_s(len(frame), level)
        if wait:
            time.sleep(air + TX_MARGIN_S)
        return SendInfo(mode, len(frame), air)

    def receive(self, duration: Optional[float] = None, idle: float = 0.15,
                drssi: Optional[bool] = None) -> Iterator[Packet]:
        """Receives packets. The method is a generator.

        The module must not be in AT mode while it receives. The method reads
        DRSSI in AT mode if the caller does not give it, then leaves AT mode.
        The UART has no packet marker. The method ends a packet after a quiet
        time of `idle` seconds.

        Args:
            duration: The time to listen in seconds. None means no limit.
            idle: The quiet time that ends a packet, in seconds.
            drssi: True if the module appends an RSSI byte. None means read
                the setting from the module.

        Yields:
            Each received packet.

        Raises:
            ModuleError: The module does not answer the setup commands.
        """
        if drssi is None:
            drssi = bool(int(self.query("drssi")))
        self.exit_at()
        time.sleep(START_DELAY_S)
        self._ser.reset_input_buffer()
        end = None if duration is None else time.monotonic() + duration
        buf = b""
        last_rx = time.monotonic()
        while True:
            now = time.monotonic()
            if end is not None and now >= end:
                break
            chunk = self._ser.read(self._ser.in_waiting or 1)
            if chunk:
                buf += chunk
                last_rx = time.monotonic()
                continue
            if buf and now - last_rx >= idle:
                yield self._make_packet(buf, drssi)
                buf = b""
            time.sleep(0.002)
        if buf:
            yield self._make_packet(buf, drssi)

    @staticmethod
    def _make_packet(raw: bytes, drssi: bool) -> Packet:
        """Builds a Packet from the bytes of one burst.

        Args:
            raw: The received bytes.
            drssi: True if the last byte is the RSSI byte.

        Returns:
            The packet.
        """
        if drssi and raw:
            return Packet(raw[:-1], decode_rssi(raw[-1]), time.time())
        return Packet(raw, None, time.time())

    def noise_scan(self, channels: list[int], restore: bool = True,
                   settle: float = 0.2) -> list[tuple[int, int]]:
        """Measures the noise level of channels with AT+ERSSI.

        For each channel the method sets the channel, restarts the module,
        enters AT mode and reads ERSSI. The method sends no radio data.

        Args:
            channels: The channel numbers to measure.
            restore: If True, set the original channel at the end.
            settle: The time to wait after each restart in seconds.

        Returns:
            A list of (channel, noise in dBm), in the order of the input.

        Raises:
            ModuleError: The module fails during the scan.
        """
        original = self.get_channel()
        results: list[tuple[int, int]] = []
        try:
            for channel in channels:
                self.set_channel(channel)
                time.sleep(settle)
                results.append((channel, int(self.query("erssi"))))
        finally:
            if restore and self.get_channel() != original:
                self.set_channel(original)
        return results

    def reset(self) -> bool:
        """Restarts the module with AT+RESET.

        The module starts in transparent mode. A new baud rate or parity from
        an earlier set command applies to the port now.

        Returns:
            True if the module sent "Power On" after the restart.

        Raises:
            ModuleError: The module does not answer AT+RESET.
        """
        resp = self.command("AT+RESET", expect_ok=True)
        self._in_at = False
        self._reset_pending = False
        self._apply_pending_port_settings()
        return self._wait_power_on(resp.lines)

    def factory_reset(self) -> bool:
        """Restores all settings with AT+DEFAULT.

        The default UART setting is 9600 8N1. The port changes to match.

        Returns:
            True if the module sent "Power On" after the restart.

        Raises:
            ModuleError: The module does not answer AT+DEFAULT.
        """
        resp = self.command("AT+DEFAULT", expect_ok=True)
        self._in_at = False
        self._reset_pending = False
        self._pending_baud = 9600
        self._pending_parity = "n"
        self._apply_pending_port_settings()
        return self._wait_power_on(resp.lines)

    def _apply_pending_port_settings(self) -> None:
        """Applies a baud rate or parity change to the open port."""
        if self._pending_baud is not None:
            self._ser.baudrate = self._pending_baud
            self._pending_baud = None
        if self._pending_parity is not None:
            self._ser.parity = _PARITY[self._pending_parity]
            self._pending_parity = None

    def help(self) -> dict[str, str]:
        """Reads the AT+HELP block.

        Returns:
            A map from the upper case key to the value text.

        Raises:
            ModuleError: The module does not answer.
        """
        self.enter_at()

        def done(ls: list[str]) -> bool:
            if any(is_error_line(x) for x in ls):
                return True
            return sum(1 for x in ls if x.startswith("==")) >= 2

        self._write(b"AT+HELP\r\n")
        lines = self._read_until(done)
        raise_on_error(parse_response(lines))
        if not lines:
            raise ModuleError("No reply to AT+HELP.")
        return parse_help(lines)

    def raw(self, line: str) -> list[str]:
        """Sends one raw line and returns all reply lines.

        Only "AT..." lines and "+++" are allowed. Other text would go on the
        air in transparent mode.

        Args:
            line: The text, for example "AT+HELP" or "+++".

        Returns:
            The reply lines. The method stops after a short quiet time.

        Raises:
            ModuleError: The line is not an AT command or "+++".
        """
        text = line.strip()
        if text == "+++":
            lines = self._toggle()
            joined = " ".join(lines)
            if _ENTRY_RE.search(joined):
                self._in_at = True
            elif _EXIT_RE.search(joined):
                self._in_at = False
            return lines
        if not text.upper().startswith("AT"):
            raise ModuleError("Raw lines must start with AT, or be +++.")
        self.enter_at()
        self._write(text.encode("ascii") + b"\r\n")
        lines = self._read_until(
            lambda ls: any(x.upper() == "OK" or is_error_line(x) for x in ls),
            idle=0.25)
        if any(_POWER_ON_RE.search(x) for x in lines):
            self._in_at = False
        return lines
