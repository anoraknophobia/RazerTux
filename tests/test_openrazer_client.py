"""OpenRazer D-Bus client against an in-memory fake daemon."""
import asyncio

import pytest

from fake_openrazer import FakeOpenRazerBus, FakeOrDev

from razertux.openrazer import client as C
from razertux.openrazer.client import OpenRazerClient, OpenRazerError


async def test_devices_lists_pid_and_methods():
    bus = FakeOpenRazerBus({"PM1": FakeOrDev(0x00A8)})
    devs = await OpenRazerClient(bus).devices()
    assert [(d.serial, d.pid, d.path) for d in devs] == [("PM1", 0x00A8, "/org/razer/device/PM1")]
    assert "setLogoStatic" in devs[0].methods["razer.device.lighting.logo"]
    assert devs[0].has("razer.device.misc", "setPollRate", "getPollRate")


async def test_unavailable_when_no_owner():
    bus = FakeOpenRazerBus({"PM1": FakeOrDev(0x00A8)}, owner=False)
    client = OpenRazerClient(bus)
    assert await client.available() is False
    assert await client.devices() == []


async def test_broken_device_skipped():
    bus = FakeOpenRazerBus({"BAD": FakeOrDev(0x00A7, fail={"getVidPid"}),
                            "PM1": FakeOrDev(0x00A8)})
    assert [d.serial for d in await OpenRazerClient(bus).devices()] == ["PM1"]


async def test_call_times_out(monkeypatch):
    monkeypatch.setattr(C, "OPENRAZER_TIMEOUT", 0.05)
    bus = FakeOpenRazerBus({"PM1": FakeOrDev(0x00A8)})
    bus.hang = True
    with pytest.raises(OpenRazerError):
        await asyncio.wait_for(OpenRazerClient(bus).devices(), 1.0)


async def test_devices_stops_at_first_timeout(monkeypatch):
    monkeypatch.setattr(C, "OPENRAZER_TIMEOUT", 0.05)
    bus = FakeOpenRazerBus({"A": FakeOrDev(0x00A7), "B": FakeOrDev(0x00A8), "C": FakeOrDev(0x0084)})
    bus.hang_members = {"getVidPid"}
    with pytest.raises(OpenRazerError):
        await OpenRazerClient(bus).devices()
    assert [c[2] for c in bus.calls].count("getVidPid") == 1


async def test_owner_changed_callback():
    bus = FakeOpenRazerBus()
    seen = []
    OpenRazerClient(bus).on_availability(seen.append)
    bus.set_owner(False)
    bus.set_owner(True)
    assert seen == [False, True]
