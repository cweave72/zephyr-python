"""A simulated DX-LR22-900T22D module and serial port for tests.

FakeDevice follows the AT command rules of the application guide. It records
all data-mode bytes in `transmitted`. A test can check that the driver never
sends data over the radio.
"""
from __future__ import annotations

import re
from typing import Callable

import serial

BAUD_RATES = {1: 2400, 2: 4800, 3: 9600, 4: 19200, 5: 38400, 6: 57600,
              7: 115200}

DEFAULTS = {
    "BAUD": "3", "PARI": "0", "LEVEL": "2", "MODE": "0", "SLEEP": "2",
    "SWITCH": "0", "CHANNEL": "41", "MAC": "ff,ff", "OPENKEY": "1",
    "PACKET": "3", "DRSSI": "0", "POWE": "22", "LBT": "0", "LRSSI": "-100",
    "ERSSI": "-95", "IQ": "1", "CRC": "1", "KEY": "12345",
}

# cmd -> (lowest, highest). CHANNEL is hex. MAC has its own rule.
RANGES = {
    "BAUD": (1, 7), "PARI": (0, 2), "LEVEL": (0, 7), "MODE": (0, 2),
    "SLEEP": (0, 2), "SWITCH": (0, 1), "CHANNEL": (0, 0x63),
    "OPENKEY": (0, 1), "PACKET": (0, 3), "DRSSI": (0, 1), "POWE": (0, 22),
    "LBT": (0, 1), "LRSSI": (-255, 0), "IQ": (0, 1), "CRC": (0, 1),
    "KEY": (0, 65535),
}
QUERY_ONLY = {"ERSSI"}
SET_ONLY = {"KEY"}


class FakeDevice:
    """The module. It reads bytes from the port and writes replies."""

    def __init__(self, eerror: bool = False, silent: bool = False,
                 start_in_at: bool = False) -> None:
        """Creates the device in transparent mode with default settings.

        Args:
            eerror: If True, use the guide spelling "EEROR=" for errors.
            silent: If True, never reply.
            start_in_at: If True, start in AT mode.
        """
        self.eerror = eerror
        self.silent = silent
        self.settings = dict(DEFAULTS)
        self.active_baud = 9600
        self.mode = "at" if start_in_at else "data"
        self.out = bytearray()
        self.pending = bytearray()
        self.transmitted: list[bytes] = []
        self.peer: "FakeDevice | None" = None
        self.rssi_byte = 0xAB  # -84 dBm
        self.noise: dict[int, int] = {}  # channel -> noise in dBm

    def reply(self, text: str) -> None:
        """Queues one reply line.

        Args:
            text: The line without line end.
        """
        if not self.silent:
            self.out += text.encode() + b"\r\n"

    def error(self, code: int) -> None:
        """Queues an error line.

        Args:
            code: The error code.
        """
        self.reply(f"{'EEROR' if self.eerror else 'ERROR'}={code}")

    def restart(self) -> None:
        """Applies the stored UART settings and returns to transparent mode."""
        self.active_baud = BAUD_RATES[int(self.settings["BAUD"])]
        self.mode = "data"
        self.pending.clear()
        self.reply("Power On")

    def feed(self, data: bytes, port_baud: int) -> None:
        """Handles bytes from the host.

        Args:
            data: The bytes written to the port.
            port_baud: The baud rate of the host port.
        """
        if port_baud != self.active_baud:
            return  # Wrong baud rate: the module sees noise.
        if data == b"+++\r\n":  # A bare +++ gets no reply.
            if self.mode == "data":
                self.mode = "at"
                self.reply("Entry AT")
            else:
                self.reply("Exit AT")
                self.restart()
            return
        if self.mode == "data":
            self.transmitted.append(data)
            self.radio_tx(data)
            return
        self.pending += data
        while b"\n" in self.pending:
            raw, rest = bytes(self.pending).split(b"\n", 1)
            self.pending = bytearray(rest)
            self.handle_line(raw.decode().strip())

    def radio_tx(self, data: bytes) -> None:
        """Sends UART data over the simulated radio to the peer.

        The frame format follows the MODE setting. The peer receives the
        payload if channel, level, key and IQ match.

        Args:
            data: The bytes written to the UART in data mode.
        """
        peer = self.peer
        mode = self.settings["MODE"]
        if mode == "0":
            channel, addr, payload = int(self.settings["CHANNEL"], 16), None, data
        elif mode == "1":
            channel, addr, payload = data[2], int.from_bytes(data[:2], "big"), data[3:]
        else:
            channel, addr, payload = data[0], None, data[1:]
        if peer is None or peer.mode != "data":
            return
        same = all(self.settings[k] == peer.settings[k]
                   for k in ("LEVEL", "KEY", "IQ", "CRC"))
        if not same or int(peer.settings["CHANNEL"], 16) != channel:
            return
        if addr is not None:
            peer_mac = int(peer.settings["MAC"].replace(",", ""), 16)
            if peer_mac not in (addr, 0xFFFF):
                return
        peer.out += payload
        if peer.settings["DRSSI"] == "1":
            peer.out.append(peer.rssi_byte)

    def handle_line(self, line: str) -> None:
        """Runs one AT command line.

        Args:
            line: The command without line end.
        """
        if line == "AT":
            self.reply("OK")
        elif line == "AT+RESET":
            self.reply("OK")
            self.restart()
        elif line == "AT+DEFAULT":
            self.settings = dict(DEFAULTS)
            self.reply("OK")
            self.restart()
        elif line == "AT+HELP":
            self.help_block()
        else:
            self.setting_command(line)

    def help_block(self) -> None:
        """Queues the AT+HELP block."""
        s = self.settings
        for text in ("===================================",
                     "LoRa Parameter:",
                     "+VERSION=V1.2.3",
                     f"MODE:{s['MODE']}",
                     f"LEVEL:{s['LEVEL']} >> 2149bps",
                     f"SLEEP:{s['SLEEP']}",
                     f"Frequency:915150000hz >> {s['CHANNEL']}",
                     f"MAC:{s['MAC']}",
                     f"CRC:{s['CRC']}(true)",
                     f"IQ:{s['IQ']}(true)",
                     f"Power:{s['POWE']}dBm",
                     "==================================="):
            self.reply(text)

    def setting_command(self, line: str) -> None:
        """Runs a query or set command.

        Args:
            line: The command, for example "AT+LEVEL3".
        """
        match = re.fullmatch(r"AT\+([A-Z]+)(.*)", line)
        if not match or match.group(1) not in DEFAULTS:
            self.error(104)
            return
        cmd, arg = match.groups()
        if not arg:
            if cmd in SET_ONLY:
                self.error(104)
            elif cmd == "ERSSI":
                channel = int(self.settings["CHANNEL"], 16)
                self.reply(f"+ERSSI={self.noise.get(channel, -100)}")
            else:
                self.reply(f"+{cmd}={self.settings[cmd]}")
            return
        if cmd in QUERY_ONLY:
            self.error(104)
            return
        value = self.validate(cmd, arg)
        if value is None:
            self.error(105)
            return
        self.settings[cmd] = value
        self.reply(f"+{cmd}={value}")
        self.reply("OK")

    @staticmethod
    def validate(cmd: str, arg: str) -> str | None:
        """Checks a set argument.

        Args:
            cmd: The command name.
            arg: The argument text.

        Returns:
            The value text to store, or None if the argument is not valid.
        """
        try:
            if cmd == "MAC":
                hi, lo = arg.split(",")
                return f"{int(hi, 16):02x},{int(lo, 16):02x}"
            lo_lim, hi_lim = RANGES[cmd]
            number = int(arg, 16 if cmd == "CHANNEL" else 10)
        except ValueError:
            return None
        if not lo_lim <= number <= hi_lim:
            return None
        return f"{number:02x}" if cmd == "CHANNEL" else str(number)


class FakeSerial:
    """A pyserial-like port connected to a FakeDevice."""

    def __init__(self, device: FakeDevice, baud: int, parity: str) -> None:
        """Creates the port.

        Args:
            device: The device at the other end.
            baud: The host baud rate.
            parity: The host parity letter.
        """
        self.device = device
        self.baudrate = baud
        self.parity = {"n": serial.PARITY_NONE, "o": serial.PARITY_ODD,
                       "e": serial.PARITY_EVEN}[parity]
        self.closed = False

    @property
    def in_waiting(self) -> int:
        """The number of bytes ready to read."""
        return len(self.device.out)

    def read(self, size: int = 1) -> bytes:
        """Reads up to size bytes without waiting.

        Args:
            size: The maximum number of bytes.

        Returns:
            The bytes, or an empty bytes object.
        """
        data = bytes(self.device.out[:size])
        del self.device.out[:size]
        return data

    def write(self, data: bytes) -> int:
        """Sends bytes to the device.

        Args:
            data: The bytes.

        Returns:
            The number of bytes written.
        """
        self.device.feed(data, self.baudrate)
        return len(data)

    def flush(self) -> None:
        """Waits for the output to leave the port. It does nothing here."""

    def reset_input_buffer(self) -> None:
        """Discards unread bytes."""
        self.device.out.clear()

    def close(self) -> None:
        """Closes the port."""
        self.closed = True


def make_factory(device: FakeDevice) -> Callable[[str, int, str], FakeSerial]:
    """Creates a serial factory for DxLoraModule.

    Args:
        device: The device the ports connect to.

    Returns:
        A function with the signature (port, baud, parity) -> FakeSerial.
    """
    def factory(port: str, baud: int, parity: str) -> FakeSerial:
        return FakeSerial(device, baud, parity)
    return factory
