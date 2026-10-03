# dxlora

dxlora is a host tool for the **DX-LR22-900T22D** LoRa module (LLCC68 radio).
The module has a UART and an AT command set. The vendor test tool runs only
on Windows. This package replaces it on Linux. It has two parts:

1. **`dxlora`**. A command line tool with an interactive shell.
2. **`DxLoraModule` class**. A Python API for other applications.

The tool configures the module, reads its settings, sends and receives radio
data, and measures channel noise. The radio data path has a known problem.
See [Known problem: radio data](#known-problem-radio-data).

The source of the protocol is the "DX-LR22-900T22D application guide",
version 2.2.

## Installation

dxlora is part of the `python/` uv workspace. Run this command in `python/`:

```sh
uv sync
```

The USB adapter is a CH340. The Linux kernel includes the driver. Add your
user to the `dialout` group to open `/dev/ttyUSB0`:

```sh
sudo usermod -aG dialout $USER
```

## Wiring

Connect only these signals. M0, M1 and AUX stay open.

| Adapter | Module | Note                                                |
|---------|--------|-----------------------------------------------------|
| 3V3/5V  | VCC    | Check the supply range of the module first.         |
| GND     | GND    |                                                     |
| TXD     | RXD    | Cross the data lines.                               |
| RXD     | TXD    | Cross the data lines.                               |

**Note:** Do not run `set switch 1` with M0 and M1 open. The pins have weak
pull-ups. The module then enters sleep mode. In pin controlled sleep mode,
the serial port cannot wake the module. The tool needs `--force` for this
setting.

## Important: transparent mode sends data over the radio

After power-on, the module is in transparent mode. In this mode, every byte on
the UART goes on the air. The tool therefore sends `+++` first. The module
treats `+++` as the mode switch. After that, the tool sends only AT commands.
The shell rejects all other text.

Leaving AT mode resets the module. Each one-shot command enters AT mode, runs,
and leaves AT mode. Use `dxlora shell` to stay in AT mode.

## dxlora

The port is a required argument. There is no default.

```sh
dxlora --port /dev/ttyUSB0 <command> [ARGS]
```

| Option             | Description                                          |
|--------------------|------------------------------------------------------|
| `--port, -p`       | Serial device. Required.                             |
| `--baud, -b`       | Module UART baud rate. Default 9600.                 |
| `--parity`         | Module UART parity: `n`, `o` or `e`. Default `n`.    |
| `--timeout`        | Reply time limit in seconds. Default 1.0.            |
| `--guard`          | Quiet time around `+++` in seconds. Default 0.       |
| `--verbose, -v`    | Log all serial bytes.                                |

| Command                              | Description                                                |
|--------------------------------------|------------------------------------------------------------|
| `dump`                               | Reads and prints all settings and the firmware version.    |
| `get NAME`                           | Reads one setting.                                         |
| `set NAME VALUE [--no-reset]`        | Sets one setting. Restarts the module unless `--no-reset`. |
| `channel [VALUE]`                    | Reads or sets the channel (hex 00-63). Prints the MHz.     |
| `level [VALUE]`                      | Reads or sets the air rate level (0-7).                    |
| `power [VALUE]`                      | Reads or sets the transmit power (0-22 dBm).               |
| `mac [VALUE]`                        | Reads or sets the address, for example `0a01` or `0a,01`.  |
| `key VALUE`                          | Writes the key (0-65535). The module cannot report it.     |
| `reset`                              | Restarts the module. Pending changes become active.        |
| `factory-reset [--yes]`              | Restores the factory settings (`AT+DEFAULT`).              |
| `send --text T \| --hex H \| --file F` | Sends data over the radio. `--addr` and `--channel` (hex)  |
|                                      | apply to modes 1 and 2.                                    |
| `listen [-d SEC] [-n N] [--hex]`     | Prints received packets. `-d 0` listens until Ctrl-C.      |
| `scan [--from C] [--to C]`           | Measures the noise of each channel. Lists quiet to noisy.  |
| `shell`                              | Starts an interactive shell.                               |

Setting names for `get` and `set`: `baud`, `parity`, `level`, `mode`,
`sleep`, `switch`, `channel`, `mac`, `openkey`, `key`, `packet`, `drssi`,
`power`, `lbt`, `lrssi`, `erssi`, `iq`, `crc`. `erssi` is query only. `key`
is set only.

Most settings need a restart. The tool restarts the module after a set. Use
`--no-reset` to group several changes. Then run `reset`. A new baud rate or
parity applies to the port after the restart.

### Examples

```sh
dxlora -p /dev/ttyUSB0 dump
dxlora -p /dev/ttyUSB0 channel 0x42
dxlora -p /dev/ttyUSB0 set level 3
dxlora -p /dev/ttyUSB0 set baud 7        # Module is now at 115200.
dxlora -p /dev/ttyUSB0 -b 115200 dump
```

### Shell

```sh
dxlora -p /dev/ttyUSB0 shell
dxlora> get level
dxlora> set channel 41 --no-reset
dxlora> AT+HELP
dxlora> reset
dxlora> exit
```

The shell accepts all commands above, raw `AT...` lines and `+++`.
Type `help` for the list. `exit` leaves AT mode and closes the port.

## DxLoraModule

Use the class in your own code:

```python
from dxlora import DxLoraModule, channel_to_mhz

with DxLoraModule("/dev/ttyUSB0") as mod:
    mod.set_channel(0x42)           # Restarts the module.
    print(channel_to_mhz(mod.get_channel()))
    config = mod.read_config()      # ModuleConfig: values, errors, version
    print(config.number("level"))
```

| Method                                        | Description                                  |
|-----------------------------------------------|----------------------------------------------|
| `query(name) -> str`                          | Raw value text of a setting.                 |
| `set(name, value, reset=True, force=False)`   | Sets a setting. Returns a `SetResult`.       |
| `get_/set_channel, level, power, mac`         | Typed access to common settings.             |
| `read_config() -> ModuleConfig`               | Reads all settings and the version.          |
| `help() -> dict[str, str]`                    | Parses the `AT+HELP` block.                  |
| `command(text, expect_ok=True)`               | Sends one AT command and returns a response. |
| `raw(line) -> list[str]`                      | Sends an `AT` line or `+++`. Returns lines.  |
| `reset()`, `factory_reset()`                  | Restart / restore defaults.                  |
| `send(payload, addr, channel, wait=True)`     | Frames and sends data. Returns `SendInfo`.   |
| `receive(duration, idle, drssi)`              | Generator of `Packet` (data, rssi_dbm).      |
| `noise_scan(channels, restore=True)`          | Reads `ERSSI` for each channel.              |
| `enter_at()`, `exit_at()`, `close()`          | Mode control.                                |

Errors raise `ModuleError`. The `code` field holds the module error code
(104 instruction, 105 parameter, 106 other). The `lines` field holds the raw
reply.

## Radio data

`send` reads MODE, PACKET and LEVEL in AT mode. It builds the frame for the
mode, leaves AT mode and writes the frame. The packet limit is the PACKET size,
230 bytes by default.

| Mode | Name        | UART frame                                  |
|------|-------------|---------------------------------------------|
| 0    | transparent | The data as is.                             |
| 1    | fixed-point | Address (2 bytes), channel (1 byte), data.  |
| 2    | broadcast   | Channel (1 byte), data.                     |

`listen` leaves AT mode and prints each packet. The UART has no packet marker.
The tool ends a packet after a quiet time (`--idle`, default 0.15 s). If
`drssi` is on, the module appends one RSSI byte. The tool removes it and prints
the signal strength: dBm = -(0xFF - byte).

`scan` sets each channel, restarts the module and reads `AT+ERSSI`. It sends no
radio data. The value follows the signal level at the antenna. It works as an
RF probe: a nearby transmitter raises the reading.

### Bench setup

Two modules close together overload the receiver. The minimum power is 0 dBm.
Use `scan`-style `ERSSI` readings to check the level at the receiver. A good
level is -60 to -90 dBm. Add distance or shielding to get there. Do not
transmit with the antenna off for long.

### Known problem: radio data

Test with two modules at firmware V1.2.4 gave these results. Both modules had
the same settings. The RF level at the receiver was -82 dBm.

| Level                | Result                                                       |
|----------------------|--------------------------------------------------------------|
| 2 (default)          | No packet reaches the UART of the receiver.                  |
| 5 and 7              | Packets arrive with the right length. The bytes are wrong.   |
| 0, 1, 3, 4 and 6     | No packet in a short test.                                   |

At level 7, the first 3 bytes are wrong in the same way in each packet. A
delay after the reset, and switching off the key (`openkey 0`), did not help.
The cause is not known. Possible causes are receiver overload, module firmware,
or a difference between the two modules. Use `ERSSI` to check the RF level
before you suspect the software.

## Tests

The tests use a simulated module. They need no hardware.

```sh
.venv/bin/pytest dxlora
```

Run this command in `python/`. Do not use `python -m pytest` there. It adds
the `dxlora/` project directory to the import path, and this directory hides
the installed package.

## Errata of the application guide

The test module was checked on 2026-10-03. The code handles each point as shown.

| Guide text                              | Result                                                                 |
|-----------------------------------------|------------------------------------------------------------------------|
| Error prefix `EEROR`                    | Checked. The module sends `ERROR=`. The parser accepts both.           |
| `AT + CHANNEL01` with spaces            | Checked. The tool sends `AT+CHANNEL01`, without spaces.                |
| `+++` has no line end                   | Checked. The module needs `+++` and CR LF. A bare `+++` gets no reply. |
| `+++` reply text                        | Checked. `Entry AT`, then on exit `Exit AT` and `Power on`.            |
| `+++` guard time is not given           | Checked. No guard time is needed. `--guard` sets one.                  |
| Query replies                           | Checked. `+KEY=value` only, with no `OK`.                              |
| Set replies                             | Checked. `+KEY=value`, then `OK`.                                      |
| Hex digit case in set commands          | Checked. Lower case works, for example `AT+MAC0a,01`.                  |
| Firmware version V1.2.3                 | Checked. The test module reports V1.2.4.                               |
| `PACKET` sizes 2 and 3 are damaged      | Not checked. The tool assumes 128 and 230 bytes.                       |
| Level 2 rate: 2148 (table), 2149 (HELP) | Not checked. The tool prints the table value.                          |
| `set baud` and `set parity`             | Checked: `set baud`. 9600 to 115200 and back. Parity is not checked.   |
