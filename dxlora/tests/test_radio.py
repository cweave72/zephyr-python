"""Tests for send, receive and scan against two linked simulated modules."""
from __future__ import annotations

import threading
from typing import Callable

import pytest

from dxlora import DxLoraModule, ModuleError
from dxlora.protocol import (airtime_s, decode_rssi, frame_broadcast,
                             frame_fixed)
from fake_module import FakeDevice, make_factory


def open_module(device: FakeDevice) -> DxLoraModule:
    """Opens a module object on a fake device."""
    return DxLoraModule("fake", timeout=0.2,
                        serial_factory=make_factory(device))


@pytest.fixture
def pair() -> tuple[FakeDevice, FakeDevice]:
    """Two devices in radio range of each other."""
    a, b = FakeDevice(), FakeDevice()
    a.peer, b.peer = b, a
    return a, b


def run_later(delay: float, func: Callable[[], object]) -> threading.Timer:
    """Runs func in a thread after a delay and returns the started timer."""
    timer = threading.Timer(delay, func)
    timer.start()
    return timer


def test_decode_rssi() -> None:
    assert decode_rssi(0xAB) == -84
    assert decode_rssi(0xFF) == 0
    assert decode_rssi(0x00) == -255


def test_frames() -> None:
    assert frame_fixed(0x0001, 0x01, b"\xaa\xbb\xcc") == \
        bytes.fromhex("000101aabbcc")
    assert frame_broadcast(0x01, b"\xaa\xbb\xcc") == bytes.fromhex("01aabbcc")
    with pytest.raises(ValueError):
        frame_fixed(0x10000, 1, b"x")
    with pytest.raises(ValueError):
        frame_fixed(1, 0x64, b"x")
    with pytest.raises(ValueError):
        frame_broadcast(0x64, b"x")


def test_airtime() -> None:
    assert airtime_s(100, 2) == pytest.approx(800 / 2148)


def test_send_transparent(pair: tuple[FakeDevice, FakeDevice]) -> None:
    a, b = pair
    with open_module(a) as mod:
        info = mod.send(b"hello", wait=False)
    assert info.mode == 0
    assert info.frame_len == 5
    assert a.transmitted == [b"hello"]
    assert bytes(b.out) == b"hello"


def test_send_leaves_at_mode_first(pair: tuple[FakeDevice, FakeDevice]) -> None:
    a, _ = pair
    with open_module(a) as mod:
        mod.send(b"x", wait=False)
        assert not mod.in_at_mode
        assert a.mode == "data"


def test_send_fixed_point(pair: tuple[FakeDevice, FakeDevice]) -> None:
    a, b = pair
    a.settings["MODE"] = "1"
    b.settings["MAC"] = "00,01"
    with open_module(a) as mod:
        info = mod.send(b"abc", addr=0x0001, channel=0x41, wait=False)
    assert info.mode == 1
    assert a.transmitted == [b"\x00\x01\x41abc"]
    assert bytes(b.out) == b"abc"


def test_send_fixed_point_other_address_not_received(
        pair: tuple[FakeDevice, FakeDevice]) -> None:
    a, b = pair
    a.settings["MODE"] = "1"
    b.settings["MAC"] = "00,02"
    with open_module(a) as mod:
        mod.send(b"abc", addr=0x0001, channel=0x41, wait=False)
    assert bytes(b.out) == b""


def test_send_broadcast(pair: tuple[FakeDevice, FakeDevice]) -> None:
    a, b = pair
    a.settings["MODE"] = "2"
    with open_module(a) as mod:
        mod.send(b"abc", channel=0x41, wait=False)
    assert a.transmitted == [b"\x41abc"]
    assert bytes(b.out) == b"abc"


def test_send_other_channel_not_received(
        pair: tuple[FakeDevice, FakeDevice]) -> None:
    a, b = pair
    b.settings["CHANNEL"] = "42"
    with open_module(a) as mod:
        mod.send(b"hello", wait=False)
    assert bytes(b.out) == b""


@pytest.mark.parametrize("mode, kwargs", [
    ("0", {"addr": 1}),
    ("0", {"channel": 1}),
    ("1", {}),
    ("1", {"addr": 1}),
    ("1", {"channel": 1}),
    ("1", {"addr": 1, "channel": 0x64}),
    ("2", {}),
    ("2", {"addr": 1, "channel": 1}),
])
def test_send_argument_errors(pair: tuple[FakeDevice, FakeDevice], mode: str,
                              kwargs: dict) -> None:
    a, _ = pair
    a.settings["MODE"] = mode
    with open_module(a) as mod:
        with pytest.raises(ModuleError):
            mod.send(b"x", wait=False, **kwargs)
    assert a.transmitted == []


def test_send_rejects_empty_and_long_payload(
        pair: tuple[FakeDevice, FakeDevice]) -> None:
    a, _ = pair
    with open_module(a) as mod:
        with pytest.raises(ModuleError):
            mod.send(b"", wait=False)
        with pytest.raises(ModuleError, match="230"):
            mod.send(b"x" * 231, wait=False)
        mod.send(b"x" * 230, wait=False)
    assert a.transmitted == [b"x" * 230]


def test_receive_packet(pair: tuple[FakeDevice, FakeDevice]) -> None:
    a, b = pair

    def send() -> None:
        with open_module(a) as tx:
            tx.send(b"hello", wait=False)

    with open_module(b) as rx:
        timer = run_later(0.4, send)
        packets = list(rx.receive(duration=1.5, idle=0.1))
        timer.join()
    assert [p.data for p in packets] == [b"hello"]
    assert packets[0].rssi_dbm is None


def test_receive_with_rssi(pair: tuple[FakeDevice, FakeDevice]) -> None:
    a, b = pair
    b.settings["DRSSI"] = "1"

    def send() -> None:
        with open_module(a) as tx:
            tx.send(b"hello", wait=False)

    with open_module(b) as rx:
        timer = run_later(0.4, send)
        packets = list(rx.receive(duration=1.5, idle=0.1))
        timer.join()
    assert [p.data for p in packets] == [b"hello"]
    assert packets[0].rssi_dbm == -84


def test_receive_separates_packets_by_quiet_time() -> None:
    dev = FakeDevice()
    with open_module(dev) as rx:
        run_later(0.3, lambda: dev.out.extend(b"one"))
        run_later(0.7, lambda: dev.out.extend(b"two"))
        packets = list(rx.receive(duration=1.3, idle=0.1, drssi=False))
    assert [p.data for p in packets] == [b"one", b"two"]


def test_receive_leaves_at_mode() -> None:
    dev = FakeDevice()
    with open_module(dev) as rx:
        rx.query("level")
        assert dev.mode == "at"
        list(rx.receive(duration=0.2, drssi=False))
        assert dev.mode == "data"
    assert dev.transmitted == []


def test_noise_scan_restores_channel() -> None:
    dev = FakeDevice()
    dev.noise = {0x10: -90, 0x11: -60}
    with open_module(dev) as mod:
        results = mod.noise_scan([0x10, 0x11], settle=0.0)
        assert results == [(0x10, -90), (0x11, -60)]
        assert mod.get_channel() == 0x41
    assert dev.transmitted == []


def test_noise_scan_without_restore() -> None:
    dev = FakeDevice()
    with open_module(dev) as mod:
        mod.noise_scan([0x10], restore=False, settle=0.0)
        assert mod.get_channel() == 0x10
