"""Tests for dxlora.protocol."""
import pytest

from dxlora.protocol import (SETTINGS, ModuleError, channel_to_mhz,
                             describe_value, encode_value, mhz_to_channel,
                             parse_help, parse_response, raise_on_error)


def test_parse_set_reply() -> None:
    resp = parse_response(["+LEVEL=3", "OK"])
    assert resp.key == "LEVEL"
    assert resp.value == "3"
    assert resp.ok
    assert resp.error is None


def test_parse_mac_value_keeps_comma() -> None:
    assert parse_response(["+MAC=0a,01"]).value == "0a,01"


def test_parse_spaced_key_line() -> None:
    resp = parse_response(["+LEVEL =2"])
    assert resp.key == "LEVEL"
    assert resp.value == "2"


@pytest.mark.parametrize("line", ["ERROR=105", "EEROR=105", "error = 105"])
def test_parse_error_spellings(line: str) -> None:
    resp = parse_response([line])
    assert resp.error == 105
    with pytest.raises(ModuleError) as info:
        raise_on_error(resp)
    assert info.value.code == 105


def test_parse_help_block() -> None:
    lines = ["===================================", "LoRa Parameter:",
             "+VERSION=V1.2.3", "MODE:0", "LEVEL:2 >> 2149bps",
             "Frequency:915150000hz >> 41", "MAC:ff,ff", "CRC:1(true)",
             "Power:22dBm", "==================================="]
    info = parse_help(lines)
    assert info["VERSION"] == "V1.2.3"
    assert info["LEVEL"] == "2 >> 2149bps"
    assert info["FREQUENCY"].endswith(">> 41")
    assert info["MAC"] == "ff,ff"


def test_channel_frequency() -> None:
    assert channel_to_mhz(0x41) == 915.15
    assert channel_to_mhz(0) == 850.15
    assert channel_to_mhz(0x63) == 949.15
    assert mhz_to_channel(915.15) == 0x41
    with pytest.raises(ValueError):
        channel_to_mhz(0x64)
    with pytest.raises(ValueError):
        mhz_to_channel(1000.0)


@pytest.mark.parametrize("name, text, expected", [
    ("channel", "41", "41"),
    ("channel", "0x41", "41"),
    ("channel", "5", "05"),
    ("mac", "0a01", "0a,01"),
    ("mac", "0A,01", "0a,01"),
    ("mac", "0x0a01", "0a,01"),
    ("level", "3", "3"),
    ("crc", "on", "1"),
    ("crc", "0", "0"),
    ("power", "10", "10"),
    ("lrssi", "-100", "-100"),
    ("key", "65535", "65535"),
])
def test_encode_value(name: str, text: str, expected: str) -> None:
    assert encode_value(SETTINGS[name], text) == expected


@pytest.mark.parametrize("name, text", [
    ("channel", "64"), ("channel", "zz"), ("mac", "0a"), ("level", "8"),
    ("level", "-1"), ("crc", "2"), ("power", "23"), ("key", "65536"),
    ("lrssi", "1"),
])
def test_encode_value_rejects(name: str, text: str) -> None:
    with pytest.raises(ValueError):
        encode_value(SETTINGS[name], text)


def test_describe_value() -> None:
    assert "2148 bit/s" in describe_value(SETTINGS["level"], "2")
    assert describe_value(SETTINGS["channel"], "41") == "915.15 MHz"
    assert describe_value(SETTINGS["baud"], "3") == "9600 baud"
    assert describe_value(SETTINGS["crc"], "1") == "on"
    assert describe_value(SETTINGS["level"], "x") == ""


def test_switch_has_guard() -> None:
    assert SETTINGS["switch"].guard
