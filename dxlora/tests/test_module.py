"""Tests for dxlora.module against the simulated module."""
from __future__ import annotations

import pytest

from dxlora import DxLoraModule, ModuleError
from fake_module import FakeDevice, make_factory


def open_module(device: FakeDevice, timeout: float = 0.2) -> DxLoraModule:
    """Opens a module object on a fake device."""
    return DxLoraModule("fake", timeout=timeout,
                        serial_factory=make_factory(device))


@pytest.fixture
def device() -> FakeDevice:
    return FakeDevice()


def test_query_enters_at_mode(device: FakeDevice) -> None:
    with open_module(device) as mod:
        assert mod.query("level") == "2"
        assert device.mode == "at"
        assert mod.in_at_mode


def test_enter_at_when_module_already_in_at_mode() -> None:
    device = FakeDevice(start_in_at=True)
    with open_module(device) as mod:
        mod.enter_at()
        assert device.mode == "at"
        assert mod.query("channel") == "41"


def test_close_leaves_at_mode(device: FakeDevice) -> None:
    with open_module(device) as mod:
        mod.query("level")
    assert device.mode == "data"


def test_set_resets_module(device: FakeDevice) -> None:
    with open_module(device) as mod:
        result = mod.set("level", "3")
        assert result.reset_done
        assert result.needs_reset
        assert device.settings["LEVEL"] == "3"
        assert device.mode == "data"
        assert not mod.in_at_mode
        assert mod.query("level") == "3"


def test_set_without_reset_stays_pending(device: FakeDevice) -> None:
    with open_module(device) as mod:
        result = mod.set("level", "4", reset=False)
        assert not result.reset_done
        assert mod.reset_pending
        assert device.mode == "at"
        mod.reset()
        assert not mod.reset_pending


def test_device_error_code(device: FakeDevice) -> None:
    with open_module(device) as mod:
        with pytest.raises(ModuleError) as info:
            mod.command("AT+LEVEL9")
        assert info.value.code == 105
        with pytest.raises(ModuleError) as info:
            mod.command("AT+NOPE")
        assert info.value.code == 104


def test_eerror_spelling() -> None:
    device = FakeDevice(eerror=True)
    with open_module(device) as mod:
        with pytest.raises(ModuleError) as info:
            mod.command("AT+LEVEL9")
        assert info.value.code == 105


def test_invalid_value_rejected_before_sending(device: FakeDevice) -> None:
    with open_module(device) as mod:
        with pytest.raises(ModuleError):
            mod.set("level", "9")
        assert device.settings["LEVEL"] == "2"


def test_unknown_and_unreadable_setting(device: FakeDevice) -> None:
    with open_module(device) as mod:
        with pytest.raises(ModuleError):
            mod.query("nope")
        with pytest.raises(ModuleError):
            mod.query("key")
        with pytest.raises(ModuleError):
            mod.set("erssi", "1")


def test_switch_needs_force(device: FakeDevice) -> None:
    with open_module(device) as mod:
        with pytest.raises(ModuleError, match="--force"):
            mod.set("switch", "1")
        assert device.settings["SWITCH"] == "0"
        mod.set("switch", "1", force=True)
        assert device.settings["SWITCH"] == "1"


def test_baud_change_reopens_port_rate(device: FakeDevice) -> None:
    with open_module(device) as mod:
        mod.set("baud", "7")
        assert device.active_baud == 115200
        assert mod._ser.baudrate == 115200
        assert mod.query("level") == "2"


def test_factory_reset_restores_9600(device: FakeDevice) -> None:
    with open_module(device) as mod:
        mod.set("baud", "7")
        mod.set("level", "5")
        mod.factory_reset()
        assert device.settings["LEVEL"] == "2"
        assert mod._ser.baudrate == 9600
        assert mod.query("level") == "2"


def test_help_block(device: FakeDevice) -> None:
    with open_module(device) as mod:
        info = mod.help()
        assert info["VERSION"] == "V1.2.3"
        assert info["MODE"] == "0"


def test_read_config(device: FakeDevice) -> None:
    with open_module(device) as mod:
        config = mod.read_config()
    assert config.version == "V1.2.3"
    assert config.values["level"] == "2"
    assert config.number("channel") == 0x41
    assert "key" not in config.values
    assert "erssi" not in config.values
    assert not config.errors


def test_typed_accessors(device: FakeDevice) -> None:
    with open_module(device) as mod:
        mod.set_channel(0x42)
        assert mod.get_channel() == 0x42
        mod.set_level(5)
        assert mod.get_level() == 5
        mod.set_power(10)
        assert mod.get_power() == 10
        mod.set_mac(0x0A01)
        assert mod.get_mac() == 0x0A01
        assert device.settings["MAC"] == "0a,01"


def test_raw_lines(device: FakeDevice) -> None:
    with open_module(device) as mod:
        assert mod.raw("AT") == ["OK"]
        assert mod.raw("AT+LEVEL") == ["+LEVEL=2"]
        lines = mod.raw("AT+RESET")
        assert "Power On" in lines
        assert not mod.in_at_mode


def test_raw_rejects_text(device: FakeDevice) -> None:
    with open_module(device) as mod:
        with pytest.raises(ModuleError):
            mod.raw("hello")


def test_no_reply_raises() -> None:
    device = FakeDevice(silent=True)
    with open_module(device, timeout=0.1) as mod:
        with pytest.raises(ModuleError):
            mod.enter_at()


def test_no_data_ever_sent_over_the_radio(device: FakeDevice) -> None:
    with open_module(device) as mod:
        mod.read_config()
        mod.set("level", "3")
        mod.set_channel(0x10)
        mod.raw("AT+HELP")
        mod.factory_reset()
    assert device.transmitted == []
