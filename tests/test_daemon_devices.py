"""Daemon device handling with fakes: no /dev, no uinput, no session bus."""
import asyncio
import errno
import os
from types import SimpleNamespace

import pytest
from evdev import ecodes as E
from fake_output import RecordingOutput
from fake_transport import FakeTransport

from naga_config.config.store import ConfigStore
from naga_config.daemon import devices as dv
from naga_config.daemon.devices import (
    VIRTUAL_NAME,
    DeviceWatcher,
    HidrawReader,
    InputRouter,
    classify,
    source_from_links,
)
from naga_config.daemon.main import Daemon, build_parser
from naga_config.daemon.uinput_out import UinputOutput, capabilities
from naga_config.hw.buttons import DRIVER_MODE, NORMAL_MODE, ButtonId
from naga_config.razer import transport as tr
from naga_config.razer.naga_v2_pro import NagaV2Pro

BY_ID = "/dev/input/by-id/usb-Razer_Razer_Naga_V2_Pro-"


# -- fakes ------------------------------------------------------------------

def ev(t, c, v):
    return SimpleNamespace(type=t, code=c, value=v)


class FakeDev:
    def __init__(self, path, events=(), name="Razer Razer Naga V2 Pro", product=0x00A7,
                 error=None):
        self.path = path
        self.name = name
        self.info = SimpleNamespace(vendor=0x1532, product=product)
        self.grabbed = False
        self.ungrabbed = False
        self.closed = False
        self.events = list(events)
        self.error = error
        self.done = asyncio.Event()

    def grab(self):
        self.grabbed = True

    def ungrab(self):
        self.ungrabbed = True

    def close(self):
        self.closed = True

    async def async_read_loop(self):
        for e in self.events:
            yield e
        self.done.set()
        if self.error:
            raise self.error
        await asyncio.Event().wait()


class FakeMapper:
    def __init__(self, fail_on=()):
        self.calls = []
        self.released = 0
        self.fail_on = set(fail_on)

    async def handle(self, button, value):
        self.calls.append((button, value))
        if button in self.fail_on:
            raise RuntimeError("boom")

    async def release_all(self):
        self.released += 1


async def settle(dev):
    await dev.done.wait()
    for _ in range(3):
        await asyncio.sleep(0)


# -- source detection ---------------------------------------------------------

@pytest.mark.parametrize("suffix,source", [
    ("event-mouse", "mouse"),
    ("if01-event-kbd", "if01"),
    ("if02-event-kbd", "if02"),
    ("event-if01", "if01_event"),
])
def test_source_from_by_id_suffix(suffix, source):
    assert source_from_links(["/dev/input/by-path/x-event", BY_ID + suffix]) == source


def test_source_from_links_ignores_other_devices():
    assert source_from_links(["/dev/input/by-id/usb-Logitech_Mouse-event-mouse-x"]) is None
    assert source_from_links([BY_ID + "mouse"]) is None  # legacy mouseN node


def _props(iface, devname="/dev/input/event18", pid="00a7", links="", **extra):
    return {"DEVNAME": devname, "ID_VENDOR_ID": "1532", "ID_MODEL_ID": pid,
            "ID_USB_INTERFACE_NUM": iface, "DEVLINKS": links, **extra}


def test_classify_prefers_by_id_link():
    assert classify(_props("00", links=BY_ID + "if02-event-kbd")) == "if02"


@pytest.mark.parametrize("props,source", [
    (_props("00", pid="00a8", ID_INPUT_MOUSE="1"), "mouse"),
    (_props("01", pid="00a8", ID_INPUT_KEYBOARD="1"), "if01"),
    (_props("01", pid="00a8"), "if01_event"),
    (_props("02", pid="00a8", ID_INPUT_KEYBOARD="1"), "if02"),
])
def test_classify_wireless_fallback(props, source):
    assert classify(props) == source


def test_classify_rejects_foreign_and_non_event_nodes():
    assert classify({**_props("00", ID_INPUT_MOUSE="1"), "ID_VENDOR_ID": "046d"}) is None
    assert classify({**_props("00", ID_INPUT_MOUSE="1"), "ID_MODEL_ID": "0099"}) is None
    assert classify(_props("00", devname="/dev/input/mouse3", ID_INPUT_MOUSE="1")) is None
    assert classify({"DEVNAME": "/dev/input/event30", "NAME": f'"{VIRTUAL_NAME}"'}) is None


# -- watcher ---------------------------------------------------------------

class UdevDev:
    def __init__(self, props, action=None):
        self.properties = props
        self.device_node = props.get("DEVNAME")
        self.action = action


def test_watcher_scan_adds_naga_and_skips_virtual():
    devs = [
        UdevDev(_props("02", devname="/dev/input/event19", links=BY_ID + "if02-event-kbd")),
        UdevDev(_props("00", devname="/dev/input/event40", ID_INPUT_MOUSE="1")),
        UdevDev({"DEVNAME": "/dev/input/event5", "ID_VENDOR_ID": "046d"}),
    ]
    opened = {
        "/dev/input/event19": FakeDev("/dev/input/event19"),
        "/dev/input/event40": FakeDev("/dev/input/event40", name=VIRTUAL_NAME),
    }
    added, removed = [], []
    w = DeviceWatcher(lambda s, d: added.append((s, d.path)), removed.append,
                      list_devices=lambda: devs, open_device=opened.__getitem__)
    w.scan()
    assert added == [("if02", "/dev/input/event19")]
    assert opened["/dev/input/event40"].closed


def test_watcher_udev_events_add_and_remove():
    added, removed = [], []
    w = DeviceWatcher(lambda s, d: added.append((s, d.path)), removed.append,
                      list_devices=list, open_device=FakeDev)
    p = _props("00", devname="/dev/input/event18", links=BY_ID + "event-mouse")
    w.handle_udev(UdevDev(p, "add"))
    w.handle_udev(UdevDev(_props("00", devname="/dev/input/event7", pid="0001"), "remove"))
    w.handle_udev(UdevDev(p, "remove"))
    assert added == [("mouse", "/dev/input/event18")]
    assert removed == ["/dev/input/event18"]


def test_watcher_open_failure_is_logged_not_raised(caplog):
    def boom(path):
        raise PermissionError(errno.EACCES, "denied")
    w = DeviceWatcher(lambda s, d: pytest.fail("added"), lambda p: None,
                      list_devices=list, open_device=boom)
    w.handle_udev(UdevDev(_props("00", links=BY_ID + "event-mouse"), "add"))
    assert "event18" in caplog.text


# -- router ------------------------------------------------------------------

async def test_router_grabs_and_maps_buttons():
    mapper, out = FakeMapper(), RecordingOutput()
    r = InputRouter(mapper, out)
    dev = FakeDev("/dev/input/event19", [
        ev(E.EV_MSC, E.MSC_SCAN, 0x7001e), ev(E.EV_KEY, E.KEY_1, 1), ev(E.EV_SYN, 0, 0),
        ev(E.EV_KEY, E.KEY_1, 2), ev(E.EV_KEY, E.KEY_1, 0), ev(E.EV_SYN, 0, 0),
    ])
    r.add("if02", dev)
    assert dev.grabbed
    await settle(dev)
    assert mapper.calls == [(ButtonId.SIDE_1, 1), (ButtonId.SIDE_1, 2), (ButtonId.SIDE_1, 0)]
    assert out.events == []  # nothing forwarded, no stray sync
    await r.close()


async def test_router_forwards_motion_and_skips_hi_res():
    mapper, out = FakeMapper(), RecordingOutput()
    r = InputRouter(mapper, out)
    dev = FakeDev("/dev/input/event18", [
        ev(E.EV_REL, E.REL_X, 5), ev(E.EV_REL, E.REL_Y, -3), ev(E.EV_SYN, 0, 0),
        ev(E.EV_REL, E.REL_WHEEL, 1), ev(E.EV_REL, E.REL_WHEEL_HI_RES, 120), ev(E.EV_SYN, 0, 0),
        ev(E.EV_KEY, E.BTN_SIDE, 1), ev(E.EV_SYN, 0, 0),
    ])
    r.add("mouse", dev)
    await settle(dev)
    assert out.events == [("rel", E.REL_X, 5), ("rel", E.REL_Y, -3), ("sync",),
                          ("key", E.BTN_SIDE, 1), ("sync",)]
    assert mapper.calls == [(ButtonId.WHEEL_UP, 1)]
    await r.close()


async def test_router_survives_handler_exception(caplog):
    mapper, out = FakeMapper(fail_on={ButtonId.SIDE_1}), RecordingOutput()
    r = InputRouter(mapper, out)
    dev = FakeDev("/dev/input/event19", [ev(E.EV_KEY, E.KEY_1, 1), ev(E.EV_KEY, E.KEY_2, 1)])
    r.add("if02", dev)
    await settle(dev)
    assert mapper.calls == [(ButtonId.SIDE_1, 1), (ButtonId.SIDE_2, 1)]
    assert "boom" in caplog.text
    await r.close()


async def test_router_enodev_releases_and_drops_device():
    mapper, out = FakeMapper(), RecordingOutput()
    r = InputRouter(mapper, out)
    dev = FakeDev("/dev/input/event19", [ev(E.EV_KEY, E.KEY_1, 1)],
                  error=OSError(errno.ENODEV, "No such device"))
    r.add("if02", dev)
    await settle(dev)
    assert mapper.released == 1
    assert r.paths == set()
    assert dev.closed and not dev.ungrabbed
    await r.close()


async def test_router_remove_releases_all():
    mapper, out = FakeMapper(), RecordingOutput()
    r = InputRouter(mapper, out)
    dev = FakeDev("/dev/input/event19")
    r.add("if02", dev)
    await r.remove("/dev/input/event19")
    assert mapper.released == 1
    assert r.paths == set()
    await r.close()


async def test_router_grab_failure_skips_device(caplog):
    mapper, out = FakeMapper(), RecordingOutput()
    r = InputRouter(mapper, out)
    dev = FakeDev("/dev/input/event19")
    dev.grab = lambda: (_ for _ in ()).throw(OSError(errno.EBUSY, "busy"))
    r.add("if02", dev)
    assert r.paths == set() and dev.closed
    await r.close()


async def test_router_close_ungrabs_and_closes():
    mapper, out = FakeMapper(), RecordingOutput()
    r = InputRouter(mapper, out)
    dev = FakeDev("/dev/input/event19")
    r.add("if02", dev)
    await r.close()
    assert dev.ungrabbed and dev.closed and mapper.released == 1


async def test_router_vendor_reports_top_buttons():
    mapper, out = FakeMapper(), RecordingOutput()
    r = InputRouter(mapper, out)
    await r.vendor_report(bytes([0x04, 0x57]) + bytes(14))
    await r.vendor_report(bytes([0x05, 0x31, 0xC1, 1, 1, 1]))  # ignored notification
    await r.vendor_report(bytes([0x04, 0x00]) + bytes(14))
    await r.vendor_report(bytes([0x04, 0x52]) + bytes(14))
    await r.vendor_report(bytes([0x04, 0x00]) + bytes(14))
    assert mapper.calls == [(ButtonId.TOP_1, 1), (ButtonId.TOP_1, 0),
                            (ButtonId.TOP_2, 1), (ButtonId.TOP_2, 0)]


# -- hidraw reader -----------------------------------------------------------

async def test_hidraw_reader_delivers_reports_and_stops_on_error():
    rfd, wfd = os.pipe()
    got = []

    async def on_report(data):
        got.append(data)

    reader = HidrawReader("/dev/hidraw4", on_report, opener=lambda p, f: rfd)
    reader.start()
    os.write(wfd, bytes([0x04, 0x57, 0, 0]))
    for _ in range(50):
        await asyncio.sleep(0.01)
        if got:
            break
    assert got == [bytes([0x04, 0x57, 0, 0])]
    os.close(wfd)  # EOF -> reader stops by itself
    for _ in range(50):
        await asyncio.sleep(0.01)
        if not reader.running:
            break
    assert not reader.running
    await reader.close()


# -- uinput output -----------------------------------------------------------

class FakeUInput:
    def __init__(self, events=None, name=None, **kw):
        self.caps, self.name, self.kw = events, name, kw
        self.written, self.closed = [], False

    def write(self, t, c, v):
        self.written.append((t, c, v))

    def syn(self):
        self.written.append(("syn",))

    def close(self):
        self.closed = True


def test_capabilities():
    caps = capabilities()
    keys, rels = set(caps[E.EV_KEY]), set(caps[E.EV_REL])
    assert {E.KEY_A, E.KEY_F13, E.KEY_LEFTMETA, E.KEY_VOLUMEUP} <= keys
    assert set(range(E.BTN_LEFT, E.BTN_TASK + 1)) <= keys
    assert E.KEY_RESERVED not in keys and E.BTN_JOYSTICK not in keys
    assert rels == {E.REL_X, E.REL_Y, E.REL_WHEEL, E.REL_HWHEEL,
                    E.REL_WHEEL_HI_RES, E.REL_HWHEEL_HI_RES}


def test_uinput_output_writes():
    out = UinputOutput(factory=FakeUInput)
    ui = out._ui
    assert ui.name == VIRTUAL_NAME
    out.key(E.KEY_A, True)
    out.rel(E.REL_X, 4)
    out.sync()
    out.close()
    assert ui.written == [(E.EV_KEY, E.KEY_A, 1), (E.EV_REL, E.REL_X, 4), ("syn",)]
    assert ui.closed


# -- razer additions -----------------------------------------------------------

def test_set_device_mode_request():
    t = FakeTransport({(0x00, 0x04): lambda a: a[:2]})
    NagaV2Pro(t).set_device_mode(DRIVER_MODE)
    req = t.requests[0]
    assert (req.cmd_class, req.cmd_id, req.size) == (0x00, 0x04, 2)
    assert req.args[:2] == bytes([0x03, 0x00])


def test_device_mode_read():
    t = FakeTransport({(0x00, 0x84): bytes([0x03, 0x00])})
    assert NagaV2Pro(t).device_mode() == DRIVER_MODE
    assert t.requests[0].size == 2


def test_find_vendor_hidraw_none(monkeypatch):
    class Ctx:
        def list_devices(self, **kw):
            return []
    monkeypatch.setattr(tr.pyudev, "Context", Ctx)
    assert tr.find_vendor_hidraw() is None


# -- daemon --------------------------------------------------------------------

class RecOut(RecordingOutput):
    closed = False

    def close(self):
        self.closed = True


def naga_transport():
    t = FakeTransport.full()
    t.responses[(0x00, 0x04)] = lambda a: a[:2]
    return t


def make_daemon(tmp_path, transport=None, find_control=lambda: "/dev/hidraw2",
                applied=None, openrazer=False):
    t = transport or naga_transport()
    applied = applied if applied is not None else []
    d = Daemon(ConfigStore(tmp_path), RecOut(),
               transport_factory=lambda path: t,
               find_control=find_control, find_vendor=lambda: None,
               openrazer_loaded=lambda: openrazer, retry_delay=0,
               apply_device_settings=applied.append)
    return d, t


def modes(t):
    return [r.args[0] for r in t.requests if (r.cmd_class, r.cmd_id) == (0x00, 0x04)]


async def test_daemon_connect_sets_driver_mode_and_status(tmp_path):
    d, t = make_daemon(tmp_path)
    await d.start()
    mouse = FakeDev("/dev/input/event18")
    d.on_added("mouse", mouse)
    await d.settle()
    assert mouse.grabbed
    assert modes(t) == [DRIVER_MODE]
    assert d.hw_status() == {"connected": True, "connection": "wired",
                             "battery": 100, "charging": True}
    assert d.scroll_profile_supported() is True
    await d.shutdown()
    assert modes(t) == [DRIVER_MODE, NORMAL_MODE]
    assert mouse.ungrabbed and mouse.closed and d.out.closed


async def test_daemon_wireless_and_battery_unavailable(tmp_path):
    t = FakeTransport({(0x00, 0x04): lambda a: a[:2]})
    d, _ = make_daemon(tmp_path, transport=t)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18", product=0x00A8))
    await d.settle()
    assert d.hw_status() == {"connected": True, "connection": "wireless",
                             "battery": -1, "charging": False}
    assert d.scroll_profile_supported() is False
    await d.shutdown()


async def test_daemon_no_control_hidraw_still_grabs(tmp_path):
    d, t = make_daemon(tmp_path, find_control=lambda: None)
    await d.start()
    mouse = FakeDev("/dev/input/event18")
    d.on_added("mouse", mouse)
    await d.settle()
    assert mouse.grabbed and t.requests == []
    assert d.hw_status()["connected"] is False
    await d.shutdown()


async def test_daemon_unplug_releases_and_disconnects(tmp_path):
    d, t = make_daemon(tmp_path)
    await d.start()
    released = []
    orig = d.mapper.release_all

    async def spy():
        released.append(1)
        await orig()
    d.mapper.release_all = spy
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    d.on_removed("/dev/input/event18")
    await d.settle()
    assert released
    assert d.hw_status() == {"connected": False, "connection": "",
                             "battery": -1, "charging": False}
    await d.shutdown()
    assert modes(t) == [DRIVER_MODE]  # no mode reset on an unplugged mouse


async def test_daemon_start_loads_active_profile(tmp_path):
    from naga_config.config.model import KeyAction, Profile, Settings
    store = ConfigStore(tmp_path)
    store.save_profile(Profile(id="game", name="Spiel",
                               standard={ButtonId.SIDE_1: KeyAction(E.KEY_A)}))
    store.save_settings(Settings(active_profile="game"))
    d, _ = make_daemon(tmp_path)
    await d.start()
    assert d.service.active_profile_id == "game"
    out = d.out
    await d.mapper.handle(ButtonId.SIDE_1, 1)
    assert ("key", E.KEY_A, 1) in out.events
    await d.shutdown()


def test_parser_debug_flag():
    assert build_parser().parse_args(["--debug"]).debug is True
    assert build_parser().parse_args([]).debug is False


async def test_daemon_hotplug_readd(tmp_path):
    applied = []
    d, t = make_daemon(tmp_path, applied=applied)
    await d.start()
    assert applied == []  # no mouse yet
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    d.on_removed("/dev/input/event18")
    await d.settle()
    assert d.hw_status()["connected"] is False
    second = FakeDev("/dev/input/event22")
    d.on_added("mouse", second)
    await d.settle()
    assert second.grabbed
    assert modes(t) == [DRIVER_MODE, DRIVER_MODE]
    assert d.hw_status()["connected"] is True
    assert [p.id for p in applied] == ["default", "default"]
    await d.shutdown()


async def test_daemon_apply_hook_failure_is_logged(tmp_path, caplog):
    def boom(profile):
        raise RuntimeError("apply kaputt")
    d = Daemon(ConfigStore(tmp_path), RecOut(), transport_factory=lambda p: naga_transport(),
               find_control=lambda: "/dev/hidraw2", find_vendor=lambda: None,
               openrazer_loaded=lambda: False, retry_delay=0, apply_device_settings=boom)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    assert "apply kaputt" in caplog.text
    assert d.hw_status()["connected"] is True
    await d.shutdown()


async def test_daemon_activate_applies_when_connected(tmp_path):
    from naga_config.config.model import Profile
    applied = []
    d, _ = make_daemon(tmp_path, applied=applied)
    d.service._store.save_profile(Profile(id="game", name="Spiel"))
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    await d.service.activate("game")
    assert [p.id for p in applied] == ["default", "game"]
    await d.shutdown()


async def test_daemon_warns_when_openrazer_loaded(tmp_path, caplog):
    d, _ = make_daemon(tmp_path, openrazer=True)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    warnings = [r for r in caplog.records
                if r.levelname == "WARNING" and "OpenRazer" in r.getMessage()]
    assert len(warnings) == 2  # startup + connect
    await d.shutdown()


async def test_router_replaces_stale_device_on_same_path():
    mapper, out = FakeMapper(), RecordingOutput()
    r = InputRouter(mapper, out)
    old = FakeDev("/dev/input/event19")
    r.add("if02", old)
    new = FakeDev("/dev/input/event19", [ev(E.EV_KEY, E.KEY_1, 1)])
    r.add("if02", new)
    assert old.closed and old.ungrabbed
    assert new.grabbed and not new.closed
    await settle(new)
    assert mapper.released == 1
    assert mapper.calls == [(ButtonId.SIDE_1, 1)]
    assert r.paths == {"/dev/input/event19"}
    await r.close()


async def test_router_close_releases_before_ungrab():
    order = []

    class OrderMapper(FakeMapper):
        async def release_all(self):
            order.append("release")

    r = InputRouter(OrderMapper(), RecordingOutput())
    dev = FakeDev("/dev/input/event19")
    dev.ungrab = lambda: order.append("ungrab")
    dev.close = lambda: order.append("close")
    r.add("if02", dev)
    await r.close()
    assert order == ["release", "ungrab", "close"]
