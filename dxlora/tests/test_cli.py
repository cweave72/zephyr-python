"""Tests for the dxlora command line tool."""
from __future__ import annotations

import pytest
from click.testing import CliRunner, Result

import dxlora.cli as cli_mod
from fake_module import FakeDevice, make_factory


@pytest.fixture
def device(monkeypatch: pytest.MonkeyPatch) -> FakeDevice:
    dev = FakeDevice()
    monkeypatch.setattr(cli_mod, "open_serial", make_factory(dev))
    return dev


def run(args: list[str], input: str | None = None) -> Result:
    """Runs the CLI on the fake port."""
    base = ["--port", "fake", "--timeout", "0.2"]
    return CliRunner().invoke(cli_mod.cli, base + args, input=input)


def test_dump(device: FakeDevice) -> None:
    result = run(["dump"])
    assert result.exit_code == 0, result.output
    assert "V1.2.3" in result.output
    assert "915.15 MHz" in result.output
    assert "2148 bit/s" in result.output
    assert not any(x.startswith("key ") for x in result.output.splitlines())
    assert device.mode == "data"  # The CLI leaves AT mode.


def test_get(device: FakeDevice) -> None:
    result = run(["get", "level"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("level")


def test_set_resets_by_default(device: FakeDevice) -> None:
    result = run(["set", "level", "3"])
    assert result.exit_code == 0, result.output
    assert "restarted" in result.output
    assert device.settings["LEVEL"] == "3"


def test_set_no_reset(device: FakeDevice) -> None:
    result = run(["set", "level", "3", "--no-reset"])
    assert result.exit_code == 0, result.output
    assert "after the reset command" in result.output


def test_alias_channel(device: FakeDevice) -> None:
    result = run(["channel", "0x42"])
    assert result.exit_code == 0, result.output
    assert device.settings["CHANNEL"] == "42"
    assert "916.15 MHz" in run(["channel"]).output


def test_invalid_value_exit_code(device: FakeDevice) -> None:
    result = run(["set", "level", "9"])
    assert result.exit_code == 1
    assert "level" in result.output


def test_switch_blocked_without_force(device: FakeDevice) -> None:
    result = run(["set", "switch", "1"])
    assert result.exit_code == 1
    assert "--force" in result.output
    assert device.settings["SWITCH"] == "0"


def test_key(device: FakeDevice) -> None:
    assert run(["key", "777"]).exit_code == 0
    assert device.settings["KEY"] == "777"


def test_factory_reset_confirmation(device: FakeDevice) -> None:
    run(["set", "level", "5"])
    assert run(["factory-reset"], input="n\n").exit_code != 0
    assert device.settings["LEVEL"] == "5"
    assert run(["factory-reset", "--yes"]).exit_code == 0
    assert device.settings["LEVEL"] == "2"


def test_shell(device: FakeDevice) -> None:
    script = "get level\nAT\nset level 4 --no-reset\nhello\nexit\n"
    result = run(["shell"], input=script)
    assert result.exit_code == 0, result.output
    assert "OK" in result.output
    assert device.settings["LEVEL"] == "4"
    assert device.transmitted == []  # "hello" never reaches the radio.


def test_port_is_required() -> None:
    result = CliRunner().invoke(cli_mod.cli, ["dump"])
    assert result.exit_code == 2
    assert "--port" in result.output


def test_send_text(device: FakeDevice) -> None:
    result = run(["send", "--text", "hi"])
    assert result.exit_code == 0, result.output
    assert "Sent 2 bytes in mode 0" in result.output
    assert device.transmitted == [b"hi"]


def test_send_hex(device: FakeDevice) -> None:
    assert run(["send", "--hex", "aa bb 01"]).exit_code == 0
    assert device.transmitted == [b"\xaa\xbb\x01"]


def test_send_fixed_mode_options(device: FakeDevice) -> None:
    device.settings["MODE"] = "1"
    result = run(["send", "--text", "abc", "--addr", "0001", "--channel", "41"])
    assert result.exit_code == 0, result.output
    assert device.transmitted == [b"\x00\x01\x41abc"]


def test_send_needs_exactly_one_source(device: FakeDevice) -> None:
    assert run(["send"]).exit_code == 2
    assert run(["send", "--text", "a", "--hex", "00"]).exit_code == 2
    assert run(["send", "--hex", "zz"]).exit_code == 2
    assert device.transmitted == []


def test_send_mode_mismatch_is_an_error(device: FakeDevice) -> None:
    result = run(["send", "--text", "a", "--addr", "1"])
    assert result.exit_code == 1
    assert device.transmitted == []


def test_scan(device: FakeDevice) -> None:
    device.noise = {0x10: -90, 0x11: -60, 0x12: -75}
    result = run(["scan", "--from", "10", "--to", "12"])
    assert result.exit_code == 0, result.output
    rows = [x.split()[0] for x in result.output.splitlines()[1:]]
    assert rows == ["10", "12", "11"]  # Quiet to noisy.
    assert device.settings["CHANNEL"] == "41"


def test_scan_range_error(device: FakeDevice) -> None:
    assert run(["scan", "--from", "20", "--to", "10"]).exit_code == 2


def test_listen(device: FakeDevice) -> None:
    import threading
    threading.Timer(0.5, lambda: device.out.extend(b"abc")).start()
    result = run(["listen", "--duration", "1.5", "--idle", "0.1"])
    assert result.exit_code == 0, result.output
    assert "'abc'" in result.output
    assert "3 bytes" in result.output
