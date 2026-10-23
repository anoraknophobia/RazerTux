"""Daemon device handling with fakes: no /dev, no uinput, no session bus."""
import asyncio
import errno
import json
import logging
import os
from types import SimpleNamespace

import pytest
from evdev import ecodes as E
from fake_output import RecordingOutput
from fake_transport import FakeTransport

from razertux.config.store import ConfigStore
from razertux.daemon import devices as dv
from razertux.daemon.devices import (
    VIRTUAL_NAME,
    DeviceWatcher,
    HidrawReader,
    InputRouter,
    classify,
    source_from_links,
)
from razertux.daemon.main import Daemon, build_parser
from razertux.daemon.uinput_out import UinputOutput, capabilities
from razertux.hw.buttons import DRIVER_MODE, NORMAL_MODE, ButtonId
from razertux.razer import transport as tr
from razertux.razer.naga_v2_pro import NagaV2Pro

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


NAGA = {0x00A7, 0x00A8}
DA = "/dev/input/by-id/usb-Razer_Razer_DeathAdder_V2-"


def _props(iface, devname="/dev/input/event18", pid="00a7", links="", **extra):
    return {"DEVNAME": devname, "ID_VENDOR_ID": "1532", "ID_MODEL_ID": pid,
            "ID_USB_INTERFACE_NUM": iface, "DEVLINKS": links, **extra}


def test_classify_prefers_by_id_link():
    assert classify(_props("00", links=BY_ID + "if02-event-kbd"), NAGA) == (0xA7, "if02")


@pytest.mark.parametrize("props,source", [
    (_props("00", pid="00a8", ID_INPUT_MOUSE="1"), "mouse"),
    (_props("01", pid="00a8", ID_INPUT_KEYBOARD="1"), "if01"),
    (_props("01", pid="00a8"), "if01_event"),
    (_props("02", pid="00a8", ID_INPUT_KEYBOARD="1"), "if02"),
])
def test_classify_wireless_fallback(props, source):
    assert classify(props, NAGA) == (0xA8, source)


def test_classify_rejects_foreign_and_non_event_nodes():
    assert classify({**_props("00", ID_INPUT_MOUSE="1"), "ID_VENDOR_ID": "046d"}, NAGA) is None
    assert classify({**_props("00", ID_INPUT_MOUSE="1"), "ID_MODEL_ID": "0099"}, NAGA) is None
    assert classify(_props("00", devname="/dev/input/mouse3", ID_INPUT_MOUSE="1"), NAGA) is None
    assert classify({"DEVNAME": "/dev/input/event30", "NAME": f'"{VIRTUAL_NAME}"'}, NAGA) is None


def test_classify_fallback_is_conservative():
    assert classify(_props("02"), NAGA) is None  # non-keyboard, no by-id link
    assert classify(_props("03", ID_INPUT_KEYBOARD="1"), NAGA) == (0xA7, "if03")
    assert classify(_props("01"), NAGA) == (0xA7, "if01_event")
    assert classify(_props("00"), NAGA) is None


def test_classify_known_pid_from_db_set():
    props = _props("00", pid="0084", links=DA + "if01-event-kbd")
    assert classify(props, {0x84}) == (0x84, "if01")
    assert classify(props, NAGA) is None  # unknown PID


def test_source_from_links_generic_names():
    assert source_from_links([DA + "event-mouse"]) == "mouse"
    assert source_from_links([DA + "if01-event-kbd"]) == "if01"
    assert source_from_links([DA + "if01-event-mouse"]) == "if01_mouse"
    assert source_from_links([DA + "event-if01"]) == "if01_event"


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


class Later:
    """Collects call_later() callbacks; run() fires the pending ones."""

    def __init__(self):
        self.pending, self.delays = [], []

    def __call__(self, delay, fn, *args):
        self.delays.append(delay)
        self.pending.append((fn, args))

    def run(self):
        pending, self.pending = self.pending, []
        for fn, args in pending:
            fn(*args)


def test_watcher_open_failure_is_logged_not_raised(caplog):
    def boom(path):
        raise PermissionError(errno.EACCES, "denied")
    later = Later()
    w = DeviceWatcher(lambda s, d: pytest.fail("added"), lambda p: None,
                      list_devices=list, open_device=boom, call_later=later)
    w.handle_udev(UdevDev(_props("00", links=BY_ID + "event-mouse"), "add"))
    while later.pending:
        later.run()
    assert later.delays == [1.0] * 5  # ACL not applied yet at login: retried, then given up
    assert "event18" in caplog.text


def test_watcher_retries_open_until_acl_is_applied():
    attempts = []

    def open_device(path):
        attempts.append(path)
        if len(attempts) < 3:
            raise PermissionError(errno.EACCES, "denied")
        return FakeDev(path)
    added = []
    later = Later()
    w = DeviceWatcher(lambda s, d: added.append((s, d.path)), lambda p: None,
                      list_devices=list, open_device=open_device, call_later=later)
    w.handle_udev(UdevDev(_props("00", links=BY_ID + "event-mouse"), "add"))
    assert added == [] and len(later.pending) == 1
    later.run()
    later.run()
    assert added == [("mouse", "/dev/input/event18")] and later.pending == []


def test_watcher_does_not_retry_other_open_errors():
    def gone(path):
        raise FileNotFoundError(errno.ENOENT, "gone")
    later = Later()
    w = DeviceWatcher(lambda s, d: pytest.fail("added"), lambda p: None,
                      list_devices=list, open_device=gone, call_later=later)
    w.handle_udev(UdevDev(_props("00", links=BY_ID + "event-mouse"), "add"))
    assert later.pending == []


def test_watcher_retries_when_grab_fails():
    results = [False, False, True]  # on_added: grab busy twice, then ok
    added, opened = [], []

    def on_added(source, dev):
        added.append(dev.path)
        return results[len(added) - 1]

    def open_device(path):
        opened.append(FakeDev(path))
        return opened[-1]
    later = Later()
    w = DeviceWatcher(on_added, lambda p: None, list_devices=list,
                      open_device=open_device, call_later=later)
    p = _props("00", links=BY_ID + "event-mouse")
    w.handle_udev(UdevDev(p, "add"))
    later.run()
    later.run()
    assert added == ["/dev/input/event18"] * 3 and later.pending == []
    removed = []
    w._on_removed = removed.append
    w.handle_udev(UdevDev(p, "remove"))
    assert removed == ["/dev/input/event18"]  # known once the grab succeeded


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


async def test_router_passes_multi_notch_wheel_value_to_mapper():
    mapper, out = FakeMapper(), RecordingOutput()
    r = InputRouter(mapper, out)
    dev = FakeDev("/dev/input/event18", [
        ev(E.EV_REL, E.REL_WHEEL, -3), ev(E.EV_REL, E.REL_WHEEL_HI_RES, -360),
        ev(E.EV_REL, E.REL_HWHEEL, 2), ev(E.EV_SYN, 0, 0)])
    r.add("mouse", dev)
    await settle(dev)
    assert mapper.calls == [(ButtonId.WHEEL_DOWN, -3), (ButtonId.WHEEL_RIGHT, 2)]
    assert out.events == []
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
    assert r.add("if02", dev) is False
    assert r.paths == set() and dev.closed
    assert r.add("if02", FakeDev("/dev/input/event20")) is True
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


def test_virtual_device_is_named_razertux():
    assert VIRTUAL_NAME == "RazerTux virtual"


@pytest.mark.parametrize("name", ["RazerTux virtual", "naga-config virtual"])
def test_watcher_never_grabs_its_own_virtual_device(name):
    """Both names: an old naga-configd may still run while razertuxd starts."""
    devs = [UdevDev(_props("00", devname="/dev/input/event40", ID_INPUT_MOUSE="1"))]
    dev = FakeDev("/dev/input/event40", name=name)
    added = []
    w = DeviceWatcher(lambda s, d: added.append(s), lambda s: None,
                      list_devices=lambda: devs, open_device=lambda p: dev)
    w.scan()
    assert added == [] and dev.closed


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


class FakeReader:
    """Stands in for HidrawReader; tests feed reports through .report()."""

    def __init__(self, path, on_report):
        self.path, self.on_report = path, on_report
        self.started = self.closed = False

    def start(self):
        self.started = True

    async def close(self):
        self.closed = True

    async def report(self, data):
        await self.on_report(bytes(data))


def make_daemon(tmp_path, transport=None, find_control=lambda pids: "/dev/hidraw2",
                applied=None, openrazer=False, readers=None):
    t = transport or naga_transport()
    applied = applied if applied is not None else []
    extra = {"find_vendor": lambda pids: None}
    if readers is not None:
        def reader(path, cb):
            readers.append(FakeReader(path, cb))
            return readers[-1]
        extra = {"find_vendor": lambda pids: "/dev/hidraw4", "hidraw_reader": reader}
    d = Daemon(ConfigStore(tmp_path), RecOut(),
               transport_factory=lambda path: t,
               find_control=find_control,
               openrazer_loaded=lambda: openrazer, retry_delay=0,
               apply_device_settings=applied.append, **extra)
    return d, t


def naga(d):
    return d.sessions["naga_v2_pro"]


def naga_backup(root):
    return root / "backups" / "naga_v2_pro.json"


def modes(t):
    return [r.args[0] for r in t.requests if (r.cmd_class, r.cmd_id) == (0x00, 0x04)]


async def test_daemon_connect_sets_normal_mode_and_status(tmp_path):
    d, t = make_daemon(tmp_path)
    await d.start()
    mouse = FakeDev("/dev/input/event18")
    d.on_added("mouse", mouse)
    await d.settle()
    assert mouse.grabbed
    assert modes(t) == [NORMAL_MODE]  # default profile: firmware keeps the top buttons
    assert d.hw_status() == {"connected": True, "connection": "wired",
                             "battery": 100, "charging": True, "dpi": 800,
                             "scroll_stage": -1}
    assert all(c != 0x02 for c, _ in t.sent)  # never probes 0x02/0x98
    await d.shutdown()
    assert modes(t) == [NORMAL_MODE, NORMAL_MODE]
    assert mouse.ungrabbed and mouse.closed and d.out.closed


async def test_daemon_wireless_and_battery_unavailable(tmp_path):
    t = FakeTransport({(0x00, 0x04): lambda a: a[:2]})
    d, _ = make_daemon(tmp_path, transport=t)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18", product=0x00A8))
    await d.settle()
    assert d.hw_status() == {"connected": True, "connection": "wireless",
                             "battery": -1, "charging": False, "dpi": -1,
                             "scroll_stage": -1}
    await d.shutdown()


async def test_daemon_no_control_hidraw_still_grabs(tmp_path):
    d, t = make_daemon(tmp_path, find_control=lambda pids: None)
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
    orig = naga(d).mapper.release_all

    async def spy():
        released.append(1)
        await orig()
    naga(d).mapper.release_all = spy
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    d.on_removed("/dev/input/event18")
    await d.settle()
    assert released
    assert d.hw_status() == {"connected": False, "connection": "",
                             "battery": -1, "charging": False, "dpi": -1,
                             "scroll_stage": -1}
    await d.shutdown()
    assert modes(t) == [NORMAL_MODE]  # no mode reset on an unplugged mouse


class TwoNodes:
    """Charge cable (0x00A7) and dongle (0x00A8) at once: one hidraw/transport each."""
    HIDRAW = {0x00A7: "/dev/hidraw2", 0x00A8: "/dev/hidraw7"}
    VENDOR = {0x00A7: "/dev/hidraw3", 0x00A8: "/dev/hidraw8"}

    def __init__(self):
        self.control_calls, self.vendor_calls, self.opened = [], [], []

    def find_control(self, pids):
        self.control_calls.append(tuple(pids))
        return self.HIDRAW[pids[0]] if len(pids) == 1 else "/dev/hidraw2"

    def find_vendor(self, pids):
        self.vendor_calls.append(tuple(pids))
        return self.VENDOR[pids[0]] if len(pids) == 1 else "/dev/hidraw3"

    def transport(self, path):
        self.opened.append(path)
        return naga_transport()


def two_node_daemon(tmp_path):
    n = TwoNodes()
    readers = []

    def reader(path, cb):
        readers.append(FakeReader(path, cb))
        return readers[-1]
    d = Daemon(ConfigStore(tmp_path), RecOut(), transport_factory=n.transport,
               find_control=n.find_control, find_vendor=n.find_vendor,
               hidraw_reader=reader, openrazer_loaded=lambda: False, retry_delay=0,
               apply_device_settings=lambda p: None)
    return d, n, readers


async def test_unplugging_charge_cable_falls_back_to_dongle(tmp_path):
    d, n, readers = two_node_daemon(tmp_path)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18", product=0x00A8))  # dongle
    await d.settle()
    assert d.hw_status()["connection"] == "wireless"
    assert n.control_calls[-1] == (0x00A8,) and n.opened[-1] == "/dev/hidraw7"
    d.on_added("mouse", FakeDev("/dev/input/event22", product=0x00A7))  # cable plugged
    await d.settle()
    assert d.hw_status()["connection"] == "wired"
    assert n.control_calls[-1] == (0x00A7,) and n.opened[-1] == "/dev/hidraw2"
    assert n.vendor_calls[-1] == (0x00A7,) and readers[-1].path == "/dev/hidraw3"
    d.on_removed("/dev/input/event22")  # cable unplugged, dongle still there
    await d.settle()
    status = d.hw_status()
    assert status["connected"] is True and status["connection"] == "wireless"
    assert n.control_calls[-1] == (0x00A8,) and n.opened[-1] == "/dev/hidraw7"
    assert readers[-1].path == "/dev/hidraw8" and readers[-1].started
    d.on_removed("/dev/input/event18")
    await d.settle()
    assert d.hw_status()["connected"] is False
    await d.shutdown()


async def test_removing_inactive_mouse_node_keeps_connection(tmp_path):
    d, n, _ = two_node_daemon(tmp_path)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18", product=0x00A8))
    d.on_added("mouse", FakeDev("/dev/input/event22", product=0x00A7))
    await d.settle()
    opened = len(n.opened)
    d.on_removed("/dev/input/event18")  # dongle pulled while the cable is in use
    await d.settle()
    assert d.hw_status()["connection"] == "wired" and len(n.opened) == opened
    await d.shutdown()


async def test_daemon_on_added_reports_grab_result(tmp_path):
    d, _ = make_daemon(tmp_path)
    await d.start()
    busy = FakeDev("/dev/input/event18")
    busy.grab = lambda: (_ for _ in ()).throw(OSError(errno.EBUSY, "busy"))
    assert d.on_added("mouse", busy) is False
    assert d.on_added("mouse", FakeDev("/dev/input/event18")) is True
    await d.settle()
    await d.shutdown()


async def test_daemon_start_loads_active_profile(tmp_path):
    from razertux.config.model import KeyAction, Profile, Settings
    store = ConfigStore(tmp_path)
    store.save_profile(Profile(id="game", name="Spiel",
                               standard={ButtonId.SIDE_1: KeyAction(E.KEY_A)}))
    store.save_settings(Settings(active_profile="game"))
    d, _ = make_daemon(tmp_path)
    await d.start()
    assert d.service.active_profile_id == "game"
    out = d.out
    await naga(d).mapper.handle(ButtonId.SIDE_1, 1)
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
    assert modes(t) == [NORMAL_MODE, NORMAL_MODE]
    assert d.hw_status()["connected"] is True
    assert [p.id for p in applied] == ["default", "default"]
    await d.shutdown()


async def test_daemon_apply_hook_failure_is_logged(tmp_path, caplog):
    def boom(profile):
        raise RuntimeError("apply kaputt")
    d = Daemon(ConfigStore(tmp_path), RecOut(), transport_factory=lambda p: naga_transport(),
               find_control=lambda pids: "/dev/hidraw2", find_vendor=lambda pids: None,
               openrazer_loaded=lambda: False, retry_delay=0, apply_device_settings=boom)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    assert "apply kaputt" in caplog.text
    assert d.hw_status()["connected"] is True
    await d.shutdown()


async def test_daemon_activate_applies_when_connected(tmp_path):
    from razertux.config.model import Profile
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


# -- Task 7: device settings, DPI stepping, restore ----------------------------


from razertux.config.model import LightingZone, Profile  # noqa: E402
from razertux.daemon.main import apply_device_settings  # noqa: E402
from razertux.razer.naga_v2_pro import LOGO, SCROLL, THUMB  # noqa: E402


def writes(t):
    """Set-requests in send order as (class, id, args-prefix-less) tuples."""
    return [(r.cmd_class, r.cmd_id) for r in t.requests if not r.cmd_id & 0x80]


def full_profile(**kw):
    base = dict(id="p", name="P", poll_rate=1000,
                dpi_stages=[(800, 800), (1600, 1600), (3200, 3200)], active_stage=2,
                lighting={"thumb": LightingZone("static", (1, 2, 3), 50),
                          "logo": LightingZone("spectrum", (0, 0, 0), 60),
                          "scroll": LightingZone("off", (0, 0, 0), 70)})
    base.update(kw)
    return Profile(**base)


def test_apply_order_poll_stages_dpi_then_zones_effect_before_brightness():
    t = FakeTransport.full()
    apply_device_settings(NagaV2Pro(t), full_profile())
    assert writes(t) == [(0x00, 0x05), (0x04, 0x06), (0x04, 0x05),
                         (0x0F, 0x02), (0x0F, 0x04),   # logo
                         (0x0F, 0x02), (0x0F, 0x04),   # scroll
                         (0x0F, 0x02), (0x0F, 0x04)]   # thumb
    leds = [r.args[1] for r in t.requests if r.cmd_id in (0x02, 0x04) and r.cmd_class == 0x0F]
    assert leds == [LOGO, LOGO, SCROLL, SCROLL, THUMB, THUMB]
    stages = next(r for r in t.requests if (r.cmd_class, r.cmd_id) == (0x04, 0x06))
    assert stages.args[:3] == bytes([0, 2, 3])  # NOSTORE, active 2
    dpi = next(r for r in t.requests if (r.cmd_class, r.cmd_id) == (0x04, 0x05))
    assert dpi.args[:5] == bytes([0, 0x06, 0x40, 0x06, 0x40])  # active stage 1600
    assert all(r.args[0] == 0 for r in t.requests if (r.cmd_class, r.cmd_id) != (0x00, 0x05))


def test_apply_empty_profile_never_touches_mouse():
    t = FakeTransport.full()
    apply_device_settings(NagaV2Pro(t), Profile(id="e", name="E"))
    assert t.sent == []


def test_apply_skips_absent_zone_and_missing_zones():
    t = FakeTransport.full()
    p = full_profile(lighting={"scroll": LightingZone("off", (0, 0, 0), 70),
                               "logo": LightingZone("off", (0, 0, 0), 10)})
    apply_device_settings(NagaV2Pro(t), p, {"scroll"})
    leds = {r.args[1] for r in t.requests if r.cmd_class == 0x0F}
    assert leds == {LOGO}


def test_apply_each_command_guarded(caplog):
    t = FakeTransport.full()
    del t.responses[(0x00, 0x05)]
    del t.responses[(0x04, 0x06)]
    with caplog.at_level(logging.WARNING):
        apply_device_settings(NagaV2Pro(t), full_profile())
    assert (0x04, 0x05) in t.sent and (0x0F, 0x04) in t.sent
    assert "Abfragerate" in caplog.text and "DPI-Stufen" in caplog.text


def test_apply_bad_profile_value_is_logged_not_raised(caplog):
    t = FakeTransport.full()
    with caplog.at_level(logging.WARNING):
        apply_device_settings(NagaV2Pro(t), full_profile(poll_rate=250))
    assert "Abfragerate" in caplog.text
    assert (0x04, 0x06) in t.sent


async def connected(tmp_path, profile=None, transport=None, product=0x00A7, settings=None):
    t = transport or naga_transport()
    d = Daemon(ConfigStore(tmp_path), RecOut(), transport_factory=lambda p: t,
               find_control=lambda pids: "/dev/hidraw2", find_vendor=lambda pids: None,
               openrazer_loaded=lambda: False, retry_delay=0)
    if settings:
        d.service._store.save_settings(settings)
        d.service._settings = settings
    if profile:
        d.service._store.save_profile(profile)
        d.service._settings.active_profile = profile.id
        d.service._store.save_settings(d.service._settings)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18", product=product))
    await d.settle()
    return d, t


def dpi_writes(t):
    return [(r.args[1] << 8 | r.args[2]) for r in t.requests
            if (r.cmd_class, r.cmd_id) == (0x04, 0x05)]


async def test_daemon_connect_applies_profile_to_mouse(tmp_path):
    d, t = await connected(tmp_path, full_profile())
    assert (0x04, 0x06) in t.sent and (0x0F, 0x04) in t.sent
    await d.shutdown()


async def test_daemon_skips_scroll_zone_when_mouse_reports_none(tmp_path):
    t = naga_transport()
    base = t.responses[(0x0F, 0x84)]

    def brightness(a):
        if a[1] == SCROLL:
            raise OSError("no scroll led")
        return base(a)
    t.responses[(0x0F, 0x84)] = brightness
    d, t = await connected(tmp_path, full_profile(), t)
    leds = {r.args[1] for r in t.requests if (r.cmd_class, r.cmd_id) == (0x0F, 0x04)}
    assert leds == {LOGO, THUMB}
    await d.shutdown()


async def test_dpi_stepping_clamps_without_wrap(tmp_path):
    d, t = await connected(tmp_path, full_profile())  # active stage 2 of 800/1600/3200
    base = len(dpi_writes(t))
    naga(d)._on_dpi_stage("up")
    naga(d)._on_dpi_stage("up")  # clamped at last
    naga(d)._on_dpi_stage("down")
    naga(d)._on_dpi_stage("down")
    naga(d)._on_dpi_stage("down")  # clamped at first
    assert dpi_writes(t)[base:] == [3200, 1600, 800]
    last = [r for r in t.requests if (r.cmd_class, r.cmd_id) == (0x04, 0x05)][-1]
    assert last.args[0] == 0  # NOSTORE
    await d.shutdown()


async def test_dpi_stepping_falls_back_to_onboard_stages(tmp_path):
    d, t = await connected(tmp_path)  # default profile: no stages; onboard 800/1600/3200 active 2
    assert (0x04, 0x05) not in t.sent  # connect itself writes no DPI
    naga(d)._on_dpi_stage("up")
    naga(d)._on_dpi_stage("up")
    naga(d)._on_dpi_stage("down")
    assert dpi_writes(t) == [3200, 1600]
    assert (0x04, 0x06) not in t.sent  # stages themselves never rewritten
    await d.shutdown()


async def test_dpi_stage_index_resets_on_profile_switch(tmp_path):
    other = Profile(id="o", name="O", dpi_stages=[(400, 400), (500, 500)], active_stage=1)
    d, t = await connected(tmp_path, full_profile())
    d.service._store.save_profile(other)
    naga(d)._on_dpi_stage("up")
    await d.service.activate("o")
    n = len(dpi_writes(t))
    naga(d)._on_dpi_stage("up")
    assert dpi_writes(t)[n:] == [500]
    await d.service.activate("p")
    naga(d)._on_dpi_stage("down")  # reset to profile.active_stage (2) -> 800
    assert dpi_writes(t)[-1] == 800
    await d.shutdown()


async def test_dpi_stepping_error_is_logged_and_index_kept(tmp_path, caplog):
    d, t = await connected(tmp_path, full_profile())
    ok = t.responses[(0x04, 0x05)]
    t.responses[(0x04, 0x05)] = lambda a: (_ for _ in ()).throw(OSError("weg"))
    with caplog.at_level(logging.WARNING):
        naga(d)._on_dpi_stage("up")
    assert "nicht gesetzt" in caplog.text
    t.responses[(0x04, 0x05)] = ok
    naga(d)._on_dpi_stage("up")  # still goes to stage 3, index was not advanced on failure
    assert dpi_writes(t)[-1] == 3200
    await d.shutdown()


async def test_dpi_stepping_without_mouse_is_noop(tmp_path):
    d = Daemon(ConfigStore(tmp_path), RecOut(), openrazer_loaded=lambda: False)
    naga(d)._on_dpi_stage("up")  # must not raise


async def test_daemon_restore_writes_backup_with_varstore_keeps_mode(tmp_path):
    d, t = await connected(tmp_path)
    modes_before = modes(t)
    backup = {"poll_rate": 500, "dpi": [1800, 1800],
              "dpi_stages": {"active": 1, "stages": [[2500, 2500]]},
              "brightness": {"logo": 191, "scroll": None, "thumb": 191},
              "idle_time": 300, "low_battery_threshold": 5}
    assert d._restore_backup(backup) == []
    assert modes(t) == modes_before
    assert all(r.args[0] == 1 for r in t.requests
               if (r.cmd_class, r.cmd_id) in ((0x04, 0x05), (0x04, 0x06), (0x0F, 0x04)))
    await d.shutdown()


async def test_daemon_restore_without_mouse_raises_dbus_error(tmp_path):
    from dbus_next import DBusError
    d = Daemon(ConfigStore(tmp_path), RecOut(), openrazer_loaded=lambda: False)
    with pytest.raises(DBusError):
        d._restore_backup({})


async def test_restore_resets_dpi_stage_state_to_onboard(tmp_path):
    d, t = await connected(tmp_path, full_profile())  # stepping over profile stages
    assert naga(d)._stages == [(800, 800), (1600, 1600), (3200, 3200)]
    stages = bytes([1, 1, 2]) + b"".join(
        bytes([i, v >> 8, v & 0xFF, v >> 8, v & 0xFF, 0, 0]) for i, v in enumerate((400, 500), start=1))
    t.responses[(0x04, 0x83)] = stages  # what the mouse reports after the restore
    d._restore_backup({})
    assert naga(d)._stages == [(400, 400), (500, 500)] and naga(d)._stage_idx == 0
    naga(d)._on_dpi_stage("up")
    assert dpi_writes(t)[-1] == 500
    await d.shutdown()


async def test_dpi_cycle_wraps_to_first_stage(tmp_path):
    d, t = await connected(tmp_path, full_profile())  # active stage 2 of 800/1600/3200
    base = len(dpi_writes(t))
    naga(d)._on_dpi_stage("cycle")
    naga(d)._on_dpi_stage("cycle")  # wraps
    naga(d)._on_dpi_stage("cycle")
    assert dpi_writes(t)[base:] == [3200, 800, 1600]
    await d.shutdown()


async def test_dpi_cycle_single_stage_writes_nothing(tmp_path):
    d, t = await connected(tmp_path, full_profile(dpi_stages=[(900, 900)], active_stage=1))
    base = len(dpi_writes(t))
    naga(d)._on_dpi_stage("cycle")
    assert dpi_writes(t)[base:] == []
    await d.shutdown()


# -- R17: write persistent values back on clean shutdown --------------------------

BACKUP = {"poll_rate": 500, "dpi": [1800, 1800],
          "dpi_stages": {"active": 1, "stages": [[2500, 2500]]},
          "brightness": {"logo": 191, "scroll": None, "thumb": 191},
          "idle_time": 300, "low_battery_threshold": 5}


def write_backup(tmp_path):
    naga_backup(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    naga_backup(tmp_path).write_text(json.dumps(BACKUP))


def since(t, n):
    return [(r.cmd_class, r.cmd_id, bytes(r.args[:5])) for r in t.requests[n:]
            if not r.cmd_id & 0x80 and (r.cmd_class, r.cmd_id) != (0x00, 0x04)]


async def test_shutdown_writes_back_dpi_stages_and_poll_rate(tmp_path, caplog):
    write_backup(tmp_path)
    d, t = await connected(tmp_path, full_profile(lighting={}))
    n = len(t.requests)
    with caplog.at_level(logging.INFO):
        await d.shutdown()
    assert since(t, n) == [(0x00, 0x05, bytes([0x02])),                    # 500 Hz
                           (0x04, 0x06, bytes([1, 1, 1, 1, 0x09])),        # VARSTORE, 2500 (id 1)
                           (0x04, 0x05, bytes([1, 0x07, 0x08, 0x07, 0x08]))]  # VARSTORE 1800
    assert modes(t)[-1] == NORMAL_MODE
    assert "zurückgeschrieben" in caplog.text


async def test_shutdown_after_dpi_stepping_writes_back_dpi(tmp_path):
    write_backup(tmp_path)
    d, t = await connected(tmp_path)  # default profile: nothing persistent written
    naga(d)._on_dpi_stage("up")
    n = len(t.requests)
    await d.shutdown()
    assert [w[:2] for w in since(t, n)] == [(0x04, 0x05)]


async def test_shutdown_without_changes_writes_nothing(tmp_path):
    write_backup(tmp_path)
    d, t = await connected(tmp_path, full_profile(dpi_stages=[], poll_rate=None))
    n = len(t.requests)
    await d.shutdown()
    assert since(t, n) == []


async def test_shutdown_without_backup_logs_warning(tmp_path, caplog):
    naga_backup(tmp_path).mkdir(parents=True)  # unreadable (and not auto-created)
    d, t = await connected(tmp_path, full_profile(lighting={}))
    n = len(t.requests)
    with caplog.at_level(logging.WARNING):
        await d.shutdown()
    assert since(t, n) == []
    assert "Sicherung" in caplog.text


async def test_restore_clears_pending_write_back(tmp_path):
    write_backup(tmp_path)
    d, t = await connected(tmp_path, full_profile(lighting={}))
    d._restore_backup(BACKUP)
    n = len(t.requests)
    await d.shutdown()
    assert since(t, n) == []


async def test_first_connect_creates_backup_before_applying(tmp_path):
    t = naga_transport()
    applied_at = []
    d = Daemon(ConfigStore(tmp_path), RecOut(), transport_factory=lambda p: t,
               find_control=lambda pids: "/dev/hidraw2", find_vendor=lambda pids: None,
               openrazer_loaded=lambda: False, retry_delay=0,
               apply_device_settings=lambda p: applied_at.append(
                   naga_backup(tmp_path).exists()))
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    assert applied_at == [True]  # snapshot taken before the profile touched the mouse
    backup = json.loads(naga_backup(tmp_path).read_text())
    assert backup["dpi"] == [800, 800] and backup["poll_rate"] == 1000
    await d.shutdown()


async def test_existing_backup_is_never_overwritten_on_connect(tmp_path):
    write_backup(tmp_path)
    d, t = await connected(tmp_path)
    assert json.loads(naga_backup(tmp_path).read_text()) == BACKUP
    await d.shutdown()


async def test_backup_failure_on_connect_is_logged_not_fatal(tmp_path, caplog):
    t = FakeTransport({(0x00, 0x04): lambda a: a[:2]})  # no readable core values
    with caplog.at_level(logging.WARNING):
        d, _ = await connected(tmp_path, transport=t)
    assert not naga_backup(tmp_path).exists()
    assert d.hw_status()["connected"] is True
    assert "Sicherung" in caplog.text
    await d.shutdown()


def test_dpi_commands_are_persistent():
    from razertux.razer.naga_v2_pro import PERSISTENT_COMMANDS
    assert PERSISTENT_COMMANDS["set_dpi"] and PERSISTENT_COMMANDS["set_dpi_stages"]


# -- power settings and battery monitor ---------------------------------------------

def power_writes(t):
    return [(r.cmd_id, bytes(r.args[:2])) for r in t.requests
            if (r.cmd_class, r.cmd_id) in ((0x07, 0x03), (0x07, 0x01))]


async def test_power_settings_applied_on_connect_and_written_back(tmp_path):
    from razertux.config.model import Settings
    write_backup(tmp_path)
    s = Settings(active_profile="default", idle_time=600, low_battery_threshold=10)
    d, t = await connected(tmp_path, settings=s)
    assert power_writes(t) == [(0x03, bytes([0x02, 0x58])), (0x01, bytes([25]))]
    await d.shutdown()
    assert power_writes(t)[2:] == [(0x03, bytes([0x01, 0x2C])), (0x01, bytes([12]))]


async def test_power_settings_none_never_touch_mouse(tmp_path):
    d, t = await connected(tmp_path)
    assert power_writes(t) == []
    await d.shutdown()


async def test_reload_applies_power_settings(tmp_path):
    from razertux.config.model import Settings
    d, t = await connected(tmp_path)
    d.service._store.save_settings(Settings(active_profile="default", idle_time=120))
    await d.service.reload()
    await d.service.reload()  # unchanged values are not rewritten (no flash wear)
    assert power_writes(t) == [(0x03, bytes([0x00, 0x78]))]
    await d.shutdown()


async def test_wireless_connect_starts_battery_monitor(tmp_path):
    d, t = await connected(tmp_path, product=0x00A8)
    assert naga(d)._battery is not None
    statuses = []
    d.service.notify_status = lambda: statuses.append(d.hw_status())
    t.responses[(0x07, 0x80)] = bytes([0, 0x80])  # ~50 %
    naga(d)._battery.poll()
    assert statuses and statuses[-1]["battery"] == 50
    await d.shutdown()
    assert naga(d)._battery is None


async def test_wired_connect_has_no_battery_monitor(tmp_path):
    d, t = await connected(tmp_path)
    assert naga(d)._battery is None
    await d.shutdown()


async def test_low_battery_sends_desktop_notification(tmp_path):
    d, t = await connected(tmp_path, product=0x00A8)
    sent = []
    d.notify = lambda title, body: sent.append((title, body))
    t.responses[(0x07, 0x80)] = bytes([0, 25])  # ~10 % < default 15
    t.responses[(0x07, 0x84)] = bytes([0, 0])   # not charging
    naga(d)._battery.poll()
    naga(d)._battery.poll()
    assert len(sent) == 1 and "10" in sent[0][1]
    await d.shutdown()


async def test_low_battery_not_repeated_after_reconnect(tmp_path):
    d, t = await connected(tmp_path, product=0x00A8)
    sent = []
    d.notify = lambda title, body: sent.append(body)
    t.responses[(0x07, 0x80)] = bytes([0, 25])  # ~10 %
    t.responses[(0x07, 0x84)] = bytes([0, 0])
    naga(d)._battery.poll()
    d.on_removed("/dev/input/event18")
    await d.settle()
    d.on_added("mouse", FakeDev("/dev/input/event18", product=0x00A8))
    await d.settle()
    naga(d)._battery.poll()
    assert len(sent) == 1
    await d.shutdown()


# -- Task 11: device mode follows the profile, scroll stage / DPI notifications ----

def _remap_profile(pid="remap", on=True):
    from razertux.config.model import Profile
    return Profile(id=pid, name=pid, remap_top_buttons=on)


async def test_mode_follows_active_profile_on_connect_switch_and_reload(tmp_path):
    from razertux.config.model import Settings
    store = ConfigStore(tmp_path)
    store.save_profile(_remap_profile())
    store.save_settings(Settings(active_profile="remap"))
    d, t = make_daemon(tmp_path)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    assert modes(t) == [DRIVER_MODE]  # connect
    await d.service.activate("default")
    assert modes(t) == [DRIVER_MODE, NORMAL_MODE]  # switch
    await d.service.reload()
    assert modes(t) == [DRIVER_MODE, NORMAL_MODE]  # unchanged: nothing written
    store.save_profile(_remap_profile("default"))
    await d.service.reload()
    assert modes(t) == [DRIVER_MODE, NORMAL_MODE, DRIVER_MODE]  # Reload
    await d.shutdown()
    assert modes(t)[-1] == NORMAL_MODE  # clean shutdown always ends in normal mode


async def test_top_buttons_ignored_in_normal_mode(tmp_path):
    readers = []
    d, t = make_daemon(tmp_path, readers=readers)
    calls = []

    async def handle(button, value):
        calls.append((button, value))
    naga(d).mapper.handle = handle
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    reader = readers[-1]
    assert reader.started and reader.path == "/dev/hidraw4"
    await reader.report([0x04, 0x57] + [0] * 14)
    await reader.report([0x04, 0x00] + [0] * 14)
    assert calls == []  # firmware handles the top buttons in normal mode
    d.service._store.save_profile(_remap_profile("default"))
    await d.service.reload()
    await reader.report([0x04, 0x57] + [0] * 14)
    await reader.report([0x04, 0x00] + [0] * 14)
    assert calls == [(ButtonId.TOP_1, 1), (ButtonId.TOP_1, 0)]
    await d.shutdown()


@pytest.mark.parametrize("remap", [False, True])
async def test_notifications_update_status_and_persist(tmp_path, remap):
    readers = []
    d, t = make_daemon(tmp_path, readers=readers)
    if remap:
        d.service._store.save_profile(_remap_profile("default"))
    notified = []
    orig = d.service.notify_status
    d.service.notify_status = lambda: (notified.append(d.hw_status()), orig())
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    notified.clear()
    await readers[-1].report([0x05, 0x3A, 0x06, 0x86] + [0] * 12)
    assert d.hw_status()["scroll_stage"] == 6
    assert [n["scroll_stage"] for n in notified] == [6]
    assert json.loads((tmp_path / "state.json").read_text()) == {"scroll_stage": 6}
    await readers[-1].report(bytes.fromhex("050209c409c4") + bytes(10))
    assert d.hw_status()["dpi"] == 2500 and notified[-1]["dpi"] == 2500
    await readers[-1].report([0x05, 0x31, 0xC1, 1, 1, 1])  # periodic: ignored
    assert len(notified) == 2
    await d.shutdown()
    # known after a restart, even before the mouse connects
    d2, _ = make_daemon(tmp_path)
    assert d2.hw_status()["scroll_stage"] == 6


async def test_corrupt_state_file_means_unknown_stage(tmp_path):
    (tmp_path / "state.json").write_text("{kaputt")
    d, _ = make_daemon(tmp_path)
    assert d.hw_status()["scroll_stage"] == -1


# -- login race: /dev/uinput ACL may not be there yet ------------------------------

async def test_open_uinput_retries_until_available(caplog):
    from razertux.daemon.main import open_uinput
    attempts, slept = [], []

    def factory():
        attempts.append(1)
        if len(attempts) < 4:
            raise PermissionError(errno.EACCES, "denied")
        return "uinput"

    async def sleep(s):
        slept.append(s)
    with caplog.at_level(logging.WARNING):
        assert await open_uinput(factory, sleep=sleep) == "uinput"
    assert slept == [2.0, 2.0, 2.0]
    assert "uinput" in caplog.text


async def test_open_uinput_gives_up_after_timeout():
    from razertux.daemon.main import open_uinput
    slept = []

    def factory():
        raise PermissionError(errno.EACCES, "denied")

    async def sleep(s):
        slept.append(s)
    with pytest.raises(OSError):
        await open_uinput(factory, sleep=sleep)
    assert sum(slept) == 60 and set(slept) == {2.0}


# -- wireless wake-up: the mouse drops volatile settings while asleep --------------

async def test_wake_up_after_failed_polls_reapplies_mode_and_profile(tmp_path):
    from razertux.config.model import Settings
    store = ConfigStore(tmp_path)
    store.save_profile(_remap_profile())
    store.save_settings(Settings(active_profile="remap"))
    applied = []
    d, t = make_daemon(tmp_path, applied=applied)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18", product=0x00A8))
    await d.settle()
    assert modes(t) == [DRIVER_MODE] and len(applied) == 1
    battery = t.responses.pop((0x07, 0x80))
    naga(d)._battery.poll()  # asleep: no answer
    naga(d)._battery.poll()
    assert modes(t) == [DRIVER_MODE] and len(applied) == 1
    t.responses[(0x07, 0x80)] = battery
    naga(d)._battery.poll()  # awake again
    assert modes(t) == [DRIVER_MODE, DRIVER_MODE] and len(applied) == 2
    naga(d)._battery.poll()  # nothing new: no further writes
    assert modes(t) == [DRIVER_MODE, DRIVER_MODE] and len(applied) == 2
    await d.shutdown()


async def test_device_mode_mismatch_on_poll_reapplies(tmp_path):
    from razertux.config.model import Settings
    store = ConfigStore(tmp_path)
    store.save_profile(_remap_profile())
    store.save_settings(Settings(active_profile="remap"))
    applied = []
    d, t = make_daemon(tmp_path, applied=applied)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18", product=0x00A8))
    await d.settle()
    t.responses[(0x00, 0x84)] = bytes([DRIVER_MODE, 0])
    naga(d)._battery.poll()
    assert modes(t) == [DRIVER_MODE] and len(applied) == 1  # matches: nothing to do
    t.responses[(0x00, 0x84)] = bytes([NORMAL_MODE, 0])  # woke up in normal mode
    naga(d)._battery.poll()
    assert modes(t) == [DRIVER_MODE, DRIVER_MODE] and len(applied) == 2
    await d.shutdown()


def _onboard_table(active):
    dpis = [2500, 800, 1600, 3200, 6400]
    return bytes([1, active, len(dpis)]) + b"".join(
        bytes([i, d >> 8, d & 0xFF, d >> 8, d & 0xFF, 0, 0]) for i, d in enumerate(dpis, 1))


async def _connected_with_reader(tmp_path, table):
    readers = []
    t = naga_transport()
    t.responses[(0x04, 0x83)] = table
    t.responses[(0x04, 0x06)] = lambda a: a
    d, _ = make_daemon(tmp_path, transport=t, readers=readers)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event18"))
    await d.settle()
    return d, t, readers[-1]


async def test_invalid_dpi_notification_repairs_stage_and_dpi(tmp_path):
    # hardware: back top button landed on stage id 0x0b -> mouse reported 52582 DPI
    d, t, reader = await _connected_with_reader(tmp_path, _onboard_table(0x0B))
    t.requests.clear()
    await reader.report(bytes.fromhex("0502cd661640") + bytes(10))
    writes = [(r.cmd_class, r.cmd_id, bytes(r.args[:5])) for r in t.requests
              if (r.cmd_class, r.cmd_id) in ((0x04, 0x06), (0x04, 0x05))]
    assert writes[0][:2] == (0x04, 0x06) and writes[0][2][:3] == bytes([1, 1, 5])
    assert writes[1] == (0x04, 0x05, bytes([0, 0x09, 0xC4, 0x09, 0xC4]))
    assert d.hw_status()["dpi"] == 2500
    await d.shutdown()


async def test_invalid_dpi_with_valid_table_sets_active_stage_dpi(tmp_path):
    d, t, reader = await _connected_with_reader(tmp_path, _onboard_table(3))
    t.requests.clear()
    await reader.report(bytes.fromhex("0502cd66cd66") + bytes(10))
    assert (0x04, 0x06) not in [(r.cmd_class, r.cmd_id) for r in t.requests]
    dpi = [r for r in t.requests if (r.cmd_class, r.cmd_id) == (0x04, 0x05)]
    assert bytes(dpi[0].args[1:5]) == bytes([0x06, 0x40, 0x06, 0x40])  # stage 3: 1600
    assert d.hw_status()["dpi"] == 1600
    await d.shutdown()


async def test_valid_dpi_notification_writes_nothing(tmp_path):
    d, t, reader = await _connected_with_reader(tmp_path, _onboard_table(0x0B))
    t.requests.clear()
    await reader.report(bytes.fromhex("0502753075300000") + bytes(8))  # 30000 = max
    assert t.requests == [] and d.hw_status()["dpi"] == 30000
    await d.shutdown()
