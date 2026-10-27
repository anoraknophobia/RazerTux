"""Per-group routing between RazerTux's hidraw driver and the OpenRazer daemon."""
from fake_openrazer import NAGA_METHODS, FakeOpenRazerBus, FakeOrDev
from fake_transport import FakeTransport

from razertux.config.model import LightingZone
from razertux.devices.db import load_devices
from razertux.openrazer import client as C
from razertux.openrazer.client import OrDevice
from razertux.openrazer.device import OpenRazerDevice
from razertux.openrazer.routing import GROUPS, RoutedMouse
from razertux.razer.mouse import make_mouse
from razertux.razer.naga_v2_pro import LOGO, NagaV2Pro

DA_METHODS = {
    "razer.device.misc": {"getVidPid", "getPollRate", "setPollRate"},
    "razer.device.power": {"getBattery", "isCharging", "getIdleTime", "setIdleTime",
                           "getLowBatteryThreshold", "setLowBatteryThreshold"},
    "razer.device.lighting.logo": {"setLogoNone", "setLogoStatic", "setLogoSpectrum",
                                   "setLogoBrightness", "getLogoBrightness"},
}


def remote(methods=NAGA_METHODS, pid=0x00A7, bus=None, failures=None):
    bus = bus or FakeOpenRazerBus({"PM1": FakeOrDev(pid, methods)})
    dev = OrDevice("PM1", "/org/razer/device/PM1", pid,
                   {k: frozenset(v) for k, v in methods.items()})
    return OpenRazerDevice(bus, dev, on_failure=(lambda dev: failures.append(1)) if failures is not None
                           else (lambda dev: None)), bus


def naga(transport=None):
    return NagaV2Pro(transport or FakeTransport.full(), load_devices()["naga_v2_pro"])


def deathadder(transport=None):
    return make_mouse(load_devices()["death_adder_v2"], transport or FakeTransport.full(), 0x0084)


def test_naga_dpi_stays_local():
    r, _ = remote()
    m = RoutedMouse(naga(), r, "naga_v2_pro")
    assert m.owner("dpi") == "razertux"
    assert m.owner("lighting:logo") == "openrazer"
    assert m.owner("poll_rate") == "openrazer"
    assert m.owner("lighting:thumb") == "razertux"  # no OpenRazer zone for the thumb grid


def test_unmatched_device_all_local():
    m = RoutedMouse(deathadder(), None, "death_adder_v2")
    assert {m.owner(g) for g in GROUPS} == {"razertux"}


async def test_zone_owned_whole():
    r, _ = remote(DA_METHODS, pid=0x0084)
    m = RoutedMouse(deathadder(), r, "death_adder_v2")
    assert m.owner("lighting:logo") == "openrazer"
    assert m.supports("set_effect:logo:static") is True
    assert m.supports("set_effect:logo:wave") is False  # no hidraw fallback per effect


async def test_set_effect_static_calls_dbus():
    t = FakeTransport.full()
    r, bus = remote()
    m = RoutedMouse(naga(t), r, "naga_v2_pro")
    m.set_effect(LOGO, LightingZone("static", (255, 0, 0), 255))
    m.set_brightness(LOGO, 255)
    await r.drain()
    assert bus.writes() == [("razer.device.lighting.logo", "setLogoStatic", (255, 0, 0)),
                            ("razer.device.lighting.logo", "setLogoBrightness", (100.0,))]
    assert (0x0F, 0x02) not in t.sent and (0x0F, 0x04) not in t.sent


async def test_local_groups_use_hidraw():
    t = FakeTransport.full()
    r, bus = remote()
    m = RoutedMouse(naga(t), r, "naga_v2_pro")
    m.set_dpi_stages(1, [(800, 800), (1600, 1600)])
    await r.drain()
    assert (0x04, 0x06) in t.sent and bus.writes() == []


def test_owned_backup_fields():
    r, _ = remote(DA_METHODS, pid=0x0084)
    m = RoutedMouse(deathadder(), r, "death_adder_v2")
    assert m.owned_backup_fields() == {"poll_rate", "idle_time", "low_battery_threshold"}


async def test_battery_from_cache():
    r, _ = remote()
    await r.refresh()
    m = RoutedMouse(naga(), r, "naga_v2_pro")
    assert m.battery() == 50 and m.charging() is False  # percent, like RazerMouse


async def test_write_timeout_falls_back(monkeypatch):
    monkeypatch.setattr(C, "OPENRAZER_TIMEOUT", 0.05)
    failures = []
    r, bus = remote(failures=failures)
    bus.hang = True
    r.set_poll_rate(500)
    r.set_poll_rate(1000)
    await r.drain()
    assert failures == [1]
    assert r.failed is True


async def test_other_attributes_go_to_local():
    t = FakeTransport.full()
    local = naga(t)
    m = RoutedMouse(local, remote()[0], "naga_v2_pro")
    assert m.info is local.info
    assert m.supports("set_device_mode") is True


async def test_unoffered_effect_rejected_without_failure():
    import pytest

    from razertux.razer.mouse import UnsupportedFeature
    failures = []
    r, bus = remote(DA_METHODS, pid=0x0084, failures=failures)
    m = RoutedMouse(deathadder(), r, "death_adder_v2")
    with pytest.raises(UnsupportedFeature):
        m.set_effect("logo", LightingZone("wave", (0, 0, 0), 255))
    await r.drain()
    assert failures == [] and bus.writes() == []
