"""DeviceSession in "RazerTux + OpenRazer" mode (fake OpenRazer daemon, fake hidraw)."""
import logging

from fake_openrazer import FakeOpenRazerBus, FakeOrDev
from test_daemon_devices import naga_transport
from test_session import DA, DA_PID, NAGA, NAGA_PID, QueueDev, da_profile, make_session, writes

from razertux.config.model import LightingZone, Profile, Settings
from razertux.openrazer import client as C
from razertux.openrazer.client import OpenRazerClient

NAGA_DONGLE = 0x00A8
DA_METHODS = {
    "razer.device.misc": {"getVidPid", "getPollRate", "setPollRate"},
    "razer.device.lighting.logo": {"setLogoNone", "setLogoStatic", "setLogoSpectrum",
                                   "setLogoBrightness", "getLogoBrightness"},
}


def fake_bus(**devices):
    return FakeOpenRazerBus({serial: dev for serial, dev in devices.items()})


def da_bus():
    return fake_bus(DA1=FakeOrDev(DA_PID, DA_METHODS))


async def da_session(tmp_path, bus, mode="openrazer", profile=None):
    s, store, t = make_session(tmp_path, openrazer=OpenRazerClient(bus))
    store.for_model(DA).save_profile(profile or da_profile())
    store.save_settings(Settings(active_profiles={DA: (profile or da_profile()).id},
                                 device_access=mode))
    s.add_input(DA_PID, "mouse", QueueDev("/dev/input/event30", product=DA_PID))
    await s.settle()
    return s, store, t


def set_mode(store, mode):
    st = store.load_settings()
    st.device_access = mode
    store.save_settings(st)


async def test_razertux_mode_unchanged(tmp_path):
    bus = da_bus()
    s, _, t = await da_session(tmp_path, bus, mode="razertux")
    assert bus.calls == []
    assert (0x00, 0x05) in writes(t)
    assert s.access() == "razertux" and s.status()["device_access"] == "razertux"
    await s.close()


async def test_openrazer_mode_lighting_via_dbus(tmp_path):
    bus = fake_bus(PM1=FakeOrDev(NAGA_PID))
    s, store, t = make_session(tmp_path, model=NAGA, transport=naga_transport(),
                               openrazer=OpenRazerClient(bus))
    store.for_model(NAGA).save_profile(Profile(
        id="p", name="p", dpi_stages=[(800, 800), (1600, 1600)], active_stage=1,
        lighting={"logo": LightingZone("static", (1, 2, 3), 255)}))
    store.save_settings(Settings(active_profiles={NAGA: "p"}, device_access="openrazer"))
    s.add_input(NAGA_PID, "mouse", QueueDev("/dev/input/event40", product=NAGA_PID))
    await s.settle()
    assert ("razer.device.lighting.logo", "setLogoStatic", (1, 2, 3)) in bus.writes()
    assert (0x0F, 0x02) not in writes(t)  # logo not sent over hidraw
    assert (0x04, 0x06) in writes(t)  # Naga DPI stages stay on RazerTux
    assert s.access() == "openrazer"
    await s.close()


async def test_empty_fields_not_pushed(tmp_path):
    bus = da_bus()
    s, _, _ = await da_session(tmp_path, bus, profile=Profile(id="e", name="e"))
    assert bus.writes() == []
    await s.close()


async def test_owned_not_written_back(tmp_path):
    bus = da_bus()
    s, _, t = await da_session(tmp_path, bus)
    assert ("razer.device.misc", "setPollRate", (500,)) in bus.writes()
    await s.close()
    assert (0x00, 0x05) not in writes(t)  # neither applied nor written back over hidraw


async def test_unknown_device_runs_local(tmp_path, caplog):
    bus = fake_bus(OTHER=FakeOrDev(0x0999, DA_METHODS))
    with caplog.at_level(logging.WARNING):
        s, _, t = await da_session(tmp_path, bus)
    assert s.access() == "openrazer-unknown-device"
    assert (0x00, 0x05) in writes(t) and bus.writes() == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    await s.close()


async def test_daemon_disappears_and_returns(tmp_path):
    bus = da_bus()
    s, _, t = await da_session(tmp_path, bus)
    assert (0x00, 0x05) not in writes(t)
    bus.set_owner(False)
    await s.reroute()
    assert s.access() == "openrazer-unavailable"
    assert (0x00, 0x05) in writes(t)  # RazerTux took over
    bus.calls.clear()
    bus.set_owner(True)
    await s.reroute()
    await s.settle()
    assert s.access() == "openrazer"
    assert ("razer.device.misc", "setPollRate", (500,)) in bus.writes()
    await s.close()


async def test_write_timeout_falls_back_to_hidraw(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "OPENRAZER_TIMEOUT", 0.05)
    bus = da_bus()
    s, store, t = await da_session(tmp_path, bus)
    bus.hang = True
    await s.reload()
    await s.settle()
    assert s.access() == "openrazer-unavailable"
    assert (0x00, 0x05) in writes(t)
    await s.close()


async def test_switch_both_ways_live(tmp_path):
    bus = da_bus()
    s, store, t = await da_session(tmp_path, bus, mode="razertux")
    assert bus.writes() == []
    n = len(writes(t))
    set_mode(store, "openrazer")
    await s.reload()
    await s.settle()
    # poll_rate written back to the backup value over hidraw first, then owned by OpenRazer
    assert (0x00, 0x05) in writes(t)[n:]
    assert ("razer.device.misc", "setPollRate", (500,)) in bus.writes()
    assert s.access() == "openrazer"
    bus.calls.clear()
    n = len(writes(t))
    set_mode(store, "razertux")
    await s.reload()
    await s.settle()
    assert bus.writes() == [] and (0x00, 0x05) in writes(t)[n:]
    assert s.access() == "razertux"
    await s.close()


async def test_match_follows_active_pid(tmp_path):
    bus = fake_bus(WIRED=FakeOrDev(NAGA_PID), DONGLE=FakeOrDev(NAGA_DONGLE))
    s, store, _ = make_session(tmp_path, model=NAGA, transport=naga_transport(),
                               openrazer=OpenRazerClient(bus))
    store.save_settings(Settings(device_access="openrazer"))
    s.add_input(NAGA_DONGLE, "mouse", QueueDev("/dev/input/event41", product=NAGA_DONGLE))
    await s.settle()
    assert s.openrazer_serial() == "DONGLE"
    s.add_input(NAGA_PID, "mouse", QueueDev("/dev/input/event40", product=NAGA_PID))
    await s.settle()
    s.remove_input("/dev/input/event41")
    await s.settle()
    assert s.openrazer_serial() == "WIRED"
    await s.close()


async def test_switch_while_paused_applies_on_resume(tmp_path):
    bus = da_bus()
    s, store, _ = await da_session(tmp_path, bus, mode="razertux")
    await s.pause()
    set_mode(store, "openrazer")
    await s.reload()
    await s.settle()
    assert bus.writes() == []
    await s.resume()
    await s.settle()
    assert ("razer.device.misc", "setPollRate", (500,)) in bus.writes()
    await s.close()


async def test_switch_while_disconnected(tmp_path):
    bus = da_bus()
    s, store, _ = make_session(tmp_path, openrazer=OpenRazerClient(bus))
    store.for_model(DA).save_profile(da_profile())
    store.save_settings(Settings(active_profiles={DA: "dpi"}, device_access="openrazer"))
    await s.reload()
    assert bus.writes() == []
    s.add_input(DA_PID, "mouse", QueueDev("/dev/input/event30", product=DA_PID))
    await s.settle()
    assert ("razer.device.misc", "setPollRate", (500,)) in bus.writes()
    await s.close()


async def test_daemon_reroutes_sessions_when_openrazer_comes_and_goes(tmp_path):
    from test_session import RecOut, da_transport

    from razertux.config.store import ConfigStore
    from razertux.daemon.main import Daemon
    bus = da_bus()
    t = da_transport()
    store = ConfigStore(tmp_path)
    store.for_model(DA).save_profile(da_profile())
    store.save_settings(Settings(active_profiles={DA: "dpi"}, device_access="openrazer"))
    d = Daemon(store, RecOut(), transport_factory=lambda path: t,
               find_control=lambda pids: "/dev/hidraw9", find_vendor=lambda pids: None,
               openrazer_loaded=lambda: True, retry_delay=0, openrazer=OpenRazerClient(bus))
    await d.start()
    assert d.openrazer_available() is True
    d.on_added("mouse", QueueDev("/dev/input/event30", product=DA_PID))
    await d.settle()
    s = d.sessions[DA]
    assert s.access() == "openrazer"
    bus.set_owner(False)
    await d.settle()
    assert s.access() == "openrazer-unavailable" and d.openrazer_available() is False
    bus.set_owner(True)
    await d.settle()
    assert s.access() == "openrazer"
    await d.shutdown()


async def test_daemon_without_openrazer_client(tmp_path):
    from test_session import RecOut

    from razertux.config.store import ConfigStore
    from razertux.daemon.main import Daemon
    d = Daemon(ConfigStore(tmp_path), RecOut(), openrazer_loaded=lambda: False)
    await d.start()
    assert d.openrazer_available() is False
    await d.shutdown()
