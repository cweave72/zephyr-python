"""Protocol data for the DX-LR22-900T22D module.

This file holds the setting table, the value conversions and the response
parser. It does no serial I/O. The source is the "DX-LR22-900T22D application
guide" version 2.2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# The module sends "OK" after a successful set command.
OK = "OK"

# Frequency of channel 0 in MHz. Each channel step adds 1 MHz.
CHANNEL_BASE_MHZ = 850.15
CHANNEL_MAX = 0x63

ERROR_TEXT: dict[int, str] = {
    104: "Invalid instruction",
    105: "Invalid parameter",
    106: "Other error",
}

# The guide spells the error prefix "EEROR". Accept both spellings.
_ERROR_RE = re.compile(r"^(?:ERROR|EEROR)\s*=\s*(\d+)", re.IGNORECASE)
_VALUE_RE = re.compile(r"^\+\s*([A-Za-z]+)\s*=\s*(.*)$")

# LEVEL table: level -> (spreading factor, bandwidth kHz, coding rate, bit/s).
LEVELS: dict[int, tuple[int, int, str, int]] = {
    0: (11, 125, "4/8", 336),
    1: (11, 250, "4/5", 1075),
    2: (11, 500, "4/5", 2148),
    3: (8, 250, "4/5", 6250),
    4: (8, 500, "4/6", 10417),
    5: (7, 500, "4/6", 18229),
    6: (6, 500, "4/5", 37500),
    7: (5, 500, "4/5", 62500),
}

BAUD_RATES: dict[int, int] = {
    1: 2400,
    2: 4800,
    3: 9600,
    4: 19200,
    5: 38400,
    6: 57600,
    7: 115200,
}

PARITY_NAMES: dict[int, str] = {0: "none", 1: "odd", 2: "even"}
MODE_NAMES: dict[int, str] = {
    0: "transparent",
    1: "fixed-point",
    2: "broadcast",
}
SLEEP_NAMES: dict[int, str] = {
    0: "sleep",
    1: "air wake",
    2: "high aging (always receive)",
}
# The guide text for sizes 2 and 3 is damaged. Sizes 128 and 230 are inferred.
PACKET_SIZES: dict[int, int] = {0: 32, 1: 64, 2: 128, 3: 230}


class ModuleError(Exception):
    """Raised when the module reports an error or does not answer.

    Attributes:
        code: The module error code, or None for a timeout or format error.
        lines: The raw response lines received before the error.
    """

    def __init__(self, message: str, code: Optional[int] = None,
                 lines: Optional[list[str]] = None) -> None:
        """Creates the error.

        Args:
            message: The error text.
            code: The module error code, if any.
            lines: The raw response lines received.
        """
        super().__init__(message)
        self.code = code
        self.lines = lines or []


@dataclass(frozen=True)
class Setting:
    """One module setting, with its AT command and value rules.

    Attributes:
        name: The lowercase name used on the command line.
        cmd: The AT command name without the "AT+" prefix.
        kind: One of "int", "hex", "mac", "bool" or "enum".
        lo: The lowest allowed value (int, hex and enum kinds).
        hi: The highest allowed value (int, hex and enum kinds).
        reset: True if the setting needs AT+RESET to take effect.
        readable: True if a query form exists.
        writable: True if a set form exists.
        in_dump: True if the dump command queries this setting.
        guard: A warning text. The set command needs --force when not None.
        help: One line of description.
    """

    name: str
    cmd: str
    kind: str
    lo: int = 0
    hi: int = 1
    reset: bool = True
    readable: bool = True
    writable: bool = True
    in_dump: bool = True
    guard: Optional[str] = None
    help: str = ""


_SWITCH_GUARD = (
    "AT+SWITCH1 makes the M0 and M1 pins select the mode. The pins have weak "
    "pull-ups. If M0 and M1 float, the module enters sleep mode. In pin "
    "controlled sleep mode, the serial port cannot wake the module."
)

SETTINGS: dict[str, Setting] = {s.name: s for s in (
    Setting("baud", "BAUD", "enum", 1, 7, help="Serial baud rate (1-7)."),
    Setting("parity", "PARI", "enum", 0, 2, help="Serial parity (0-2)."),
    Setting("level", "LEVEL", "enum", 0, 7,
            help="Air rate and distance level (0-7)."),
    Setting("mode", "MODE", "enum", 0, 2,
            help="Transmission mode (0 transparent, 1 fixed, 2 broadcast)."),
    Setting("sleep", "SLEEP", "enum", 0, 2,
            help="Work mode (0 sleep, 1 air wake, 2 high aging)."),
    Setting("switch", "SWITCH", "bool", guard=_SWITCH_GUARD,
            help="Mode selection by M0 and M1 pins."),
    Setting("channel", "CHANNEL", "hex", 0, CHANNEL_MAX,
            help="Channel as hex 00-63. Frequency = 850.15 MHz + channel."),
    Setting("mac", "MAC", "mac", help="Device address, two hex bytes."),
    Setting("openkey", "OPENKEY", "bool", help="Key switch."),
    Setting("key", "KEY", "int", 0, 65535, readable=False, in_dump=False,
            help="Key value 0-65535. The module cannot report it."),
    Setting("packet", "PACKET", "enum", 0, 3, help="Subpacket length (0-3)."),
    Setting("drssi", "DRSSI", "bool",
            help="Append the RSSI byte to received packets."),
    Setting("power", "POWE", "int", 0, 22, help="Transmit power in dBm."),
    Setting("lbt", "LBT", "bool", help="Listen before talk."),
    Setting("lrssi", "LRSSI", "int", -255, 0,
            help="LBT listening threshold in dBm."),
    Setting("erssi", "ERSSI", "int", -255, 0, reset=False, writable=False,
            in_dump=False, help="Current channel noise level (query only)."),
    Setting("iq", "IQ", "bool", help="IQ inversion."),
    Setting("crc", "CRC", "bool", help="CRC check."),
)}


@dataclass
class Response:
    """A parsed module response.

    Attributes:
        key: The response key, for example "LEVEL" for "+LEVEL=2", or None.
        value: The text after "=" in the key line, or None.
        ok: True if the response has an "OK" line.
        error: The module error code, or None.
        lines: All non-empty response lines.
    """

    key: Optional[str] = None
    value: Optional[str] = None
    ok: bool = False
    error: Optional[int] = None
    lines: list[str] = field(default_factory=list)


def parse_response(lines: list[str]) -> Response:
    """Parses the lines of one module response.

    A key line looks like "+LEVEL=2". An error line looks like "ERROR=105".
    The function does not raise on an error line. It stores the code.

    Args:
        lines: The response lines without line ends.

    Returns:
        The parsed response.
    """
    resp = Response()
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        resp.lines.append(line)
        err = _ERROR_RE.match(line)
        if err:
            resp.error = int(err.group(1))
            continue
        if line.upper() == OK:
            resp.ok = True
            continue
        val = _VALUE_RE.match(line)
        if val and resp.key is None:
            resp.key = val.group(1).upper()
            resp.value = val.group(2).strip()
    return resp


def is_error_line(line: str) -> bool:
    """Tests whether a line is a module error line.

    Args:
        line: One response line.

    Returns:
        True if the line starts with "ERROR=" or "EEROR=".
    """
    return bool(_ERROR_RE.match(line.strip()))


def raise_on_error(resp: Response) -> None:
    """Raises ModuleError if the response holds an error code.

    Args:
        resp: The parsed response.

    Raises:
        ModuleError: The response has an error code.
    """
    if resp.error is not None:
        text = ERROR_TEXT.get(resp.error, "Unknown error")
        raise ModuleError(f"Module error {resp.error}: {text}",
                          code=resp.error, lines=resp.lines)


def parse_help(lines: list[str]) -> dict[str, str]:
    """Parses the block that AT+HELP returns.

    Lines in the block look like "+VERSION=V1.2.3", "MODE:0" or
    "Frequency:915150000hz >> 41".

    Args:
        lines: The lines of the AT+HELP response.

    Returns:
        A map from the upper case key to the value text.
    """
    out: dict[str, str] = {}
    for raw in lines:
        line = raw.strip().lstrip("+").strip()
        if not line or line.startswith("="):
            continue
        match = re.match(r"^([A-Za-z ]+?)\s*[:=]\s*(.*)$", line)
        if match:
            out[match.group(1).strip().upper()] = match.group(2).strip()
    return out


def channel_to_mhz(channel: int) -> float:
    """Converts a channel number to its frequency.

    Args:
        channel: The channel number, 0 to 0x63.

    Returns:
        The frequency in MHz.

    Raises:
        ValueError: The channel is out of range.
    """
    if not 0 <= channel <= CHANNEL_MAX:
        raise ValueError(f"Channel {channel:#x} is not in 0x00-0x63.")
    return round(CHANNEL_BASE_MHZ + channel, 2)


def mhz_to_channel(mhz: float) -> int:
    """Converts a frequency to the nearest channel number.

    Args:
        mhz: The frequency in MHz.

    Returns:
        The channel number.

    Raises:
        ValueError: The frequency is outside the channel range.
    """
    channel = round(mhz - CHANNEL_BASE_MHZ)
    if not 0 <= channel <= CHANNEL_MAX:
        raise ValueError(f"{mhz} MHz is not in the channel range.")
    return channel


def encode_value(setting: Setting, text: str) -> str:
    """Validates a user value and converts it to the AT command argument.

    Args:
        setting: The setting.
        text: The value as the user typed it.

    Returns:
        The text to append to "AT+<cmd>".

    Raises:
        ValueError: The value is not valid for the setting.
    """
    text = text.strip()
    if setting.kind == "bool":
        table = {"1": "1", "on": "1", "true": "1",
                 "0": "0", "off": "0", "false": "0"}
        if text.lower() not in table:
            raise ValueError(f"{setting.name}: use 0, 1, on or off.")
        return table[text.lower()]
    if setting.kind == "mac":
        digits = text.lower().replace("0x", "").replace(",", "").replace(":", "")
        if not re.fullmatch(r"[0-9a-f]{4}", digits):
            raise ValueError("mac: use four hex digits, for example 0a01 or 0a,01.")
        return f"{digits[:2]},{digits[2:]}"
    base = 16 if setting.kind == "hex" else 10
    try:
        number = int(text, base)
    except ValueError:
        raise ValueError(f"{setting.name}: {text!r} is not a number.") from None
    if not setting.lo <= number <= setting.hi:
        raise ValueError(f"{setting.name}: value must be in "
                         f"{setting.lo}..{setting.hi}.")
    return f"{number:02x}" if setting.kind == "hex" else str(number)


def describe_value(setting: Setting, raw: str) -> str:
    """Converts a raw module value to a readable note.

    Args:
        setting: The setting.
        raw: The value text from the module, for example "2".

    Returns:
        A short note, or an empty string if there is nothing to add.
    """
    try:
        if setting.name == "level":
            sf, bw, cr, bps = LEVELS[int(raw)]
            return f"{bps} bit/s, SF{sf}, BW {bw} kHz, CR {cr}"
        if setting.name == "channel":
            return f"{channel_to_mhz(int(raw, 16)):.2f} MHz"
        if setting.name == "baud":
            return f"{BAUD_RATES[int(raw)]} baud"
        if setting.name == "parity":
            return PARITY_NAMES[int(raw)]
        if setting.name == "mode":
            return MODE_NAMES[int(raw)]
        if setting.name == "sleep":
            return SLEEP_NAMES[int(raw)]
        if setting.name == "packet":
            return f"{PACKET_SIZES[int(raw)]} bytes"
        if setting.name in ("power", "lrssi", "erssi"):
            return "dBm"
        if setting.kind == "bool":
            return "on" if int(raw) else "off"
    except (ValueError, KeyError):
        return ""
    return ""


def decode_rssi(byte: int) -> int:
    """Converts the RSSI byte of a received packet to dBm.

    The module appends this byte when DRSSI is on. The value is
    -(0xFF - byte).

    Args:
        byte: The RSSI byte, 0 to 255.

    Returns:
        The signal strength in dBm.
    """
    return -(0xFF - byte)


def frame_fixed(addr: int, channel: int, payload: bytes) -> bytes:
    """Builds the UART frame for fixed-point mode (MODE 1).

    The frame is the receiver address (2 bytes), the receiver channel
    (1 byte) and the data.

    Args:
        addr: The receiver address, 0 to 0xFFFF.
        channel: The receiver channel, 0 to 0x63.
        payload: The data.

    Returns:
        The bytes to write to the UART.

    Raises:
        ValueError: The address or channel is out of range.
    """
    if not 0 <= addr <= 0xFFFF:
        raise ValueError(f"Address {addr:#x} is not in 0x0000-0xFFFF.")
    channel_to_mhz(channel)  # Range check.
    return addr.to_bytes(2, "big") + bytes([channel]) + payload


def frame_broadcast(channel: int, payload: bytes) -> bytes:
    """Builds the UART frame for broadcast mode (MODE 2).

    The frame is the receiver channel (1 byte) and the data.

    Args:
        channel: The receiver channel, 0 to 0x63.
        payload: The data.

    Returns:
        The bytes to write to the UART.

    Raises:
        ValueError: The channel is out of range.
    """
    channel_to_mhz(channel)  # Range check.
    return bytes([channel]) + payload


def airtime_s(nbytes: int, level: int) -> float:
    """Estimates the time a packet takes on the air.

    The estimate uses the bit rate of the LEVEL table. It ignores the
    preamble and the header. Use it only to wait for the end of a transmission.

    Args:
        nbytes: The number of bytes sent.
        level: The LEVEL setting, 0 to 7.

    Returns:
        The estimated time in seconds.
    """
    return nbytes * 8 / LEVELS[level][3]
