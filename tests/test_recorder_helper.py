"""Privileged record helper: whitelist, no persisting command, keylogger filter, EOF/idle exit.

Everything runs in-process with fake sysfs, fake transports and scripted event sources:
no pkexec, no real device node is ever opened.
"""
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from evdev import ecodes as E

from razertux.devices import db
from razertux.razer.report import STATUS_OK, RazerResponse, build_report, parse_report
from razertux.razer.transport import RazerError
from razertux.recorder import backend, helper, keyfilter, whitelist
from razertux.recorder.keyfilter import SignalFilter

ROOT = Path(__file__).resolve().parent.parent
POLICY = ROOT / "packaging" / "io.github.razertux.record.policy"


@pytest.fixture(autouse=True)
def no_real_nodes(monkeypatch):
    """The real node openers must never run in a test."""
    def boom(*a, **k):
        raise AssertionError("real device node opened in a test")
    monkeypatch.setattr(helper, "open_verified_hidraw", boom)
    monkeypatch.setattr(helper, "open_verified_events", boom)
    monkeypatch.setattr(helper.os, "access", lambda *a, **k: True)


# -- fake sysfs ------------------------------------------------------------------------

_counter = {"hidraw": 0, "event": 0}


def _w(p: Path, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


# a Razer control interface: Usage Page 0x01, Usage 0x02, Report Size 8, Report Count 90, Feature
RAZER_DESCRIPTOR = bytes([0x05, 0x01, 0x09, 0x02, 0xA1, 0x01, 0x75, 0x08, 0x95, 0x5A,
                          0x15, 0x00, 0x26, 0xFF, 0x00, 0x09, 0x00, 0xB1, 0x02, 0xC0])


def add_device(root, busport, pid, ifaces, vid="1532", descriptor=b"\x05\x01"):
    """ifaces: [(cls, [(input name, caps)])]; hidraw/event numbers unique over the whole tree."""
    d = root / "bus/usb/devices" / busport
    for k, v in (("idVendor", vid), ("idProduct", f"{pid:04x}"), ("bcdDevice", "0100"),
                 ("manufacturer", "Razer"), ("product", "Dev")):
        _w(d / k, v + "\n")
    for num, (cls, inputs) in enumerate(ifaces):
        i = d / f"{busport}:1.{num}"
        for k, v in (("bInterfaceNumber", f"{num:02x}"), ("bInterfaceClass", f"{cls:02x}"),
                     ("bInterfaceSubClass", "01"), ("bInterfaceProtocol", "02")):
            _w(i / k, v + "\n")
        if cls != 3:
            continue
        h = i / f"0003:1532:{pid:04X}.000{num}"
        h.mkdir(parents=True)
        (h / "report_descriptor").write_bytes(descriptor)
        (h / "hidraw" / f"hidraw{_counter['hidraw']}").mkdir(parents=True)
        _counter["hidraw"] += 1
        for name, caps in inputs:
            inp = h / "input" / f"input{_counter['event']}"
            _w(inp / "name", name + "\n")
            (inp / f"event{_counter['event']}").mkdir(parents=True)
            _counter["event"] += 1
            for cap, val in caps.items():
                _w(inp / "capabilities" / cap, val + "\n")


MOUSE_CAPS = {"ev": "17", "key": "1f0000 0 0 0 0", "rel": "1943"}
KBD_CAPS = {"ev": "120013", "key": "0 0 0 0 ffffffffffffffff"}
GRID_CAPS = {"ev": "120013", "key": "3ffc"}  # KEY_1 .. KEY_EQUAL only, no KEY_A


def build_sysfs(root):
    _counter.update(hidraw=0, event=0)
    add_device(root, "5-1", 0x00A7, [(3, [("Razer Naga V2 Pro", MOUSE_CAPS)]),
                                     (3, [("Razer Naga V2 Pro Keyboard", GRID_CAPS)])])  # hidraw0/1, event0/1
    add_device(root, "5-2", 0x0227, [(3, [("Razer Huntsman", KBD_CAPS)])])           # hidraw2, event2
    add_device(root, "5-3", 0xFFFE, [(3, [("Razer Mystery", GRID_CAPS)])],
               descriptor=RAZER_DESCRIPTOR)                                            # hidraw3, event3
    add_device(root, "5-4", 0x1234, [(3, [("Other Vendor", MOUSE_CAPS)])], vid="046d")  # hidraw4, event4
    return str(root)


# -- fakes -------------------------------------------------------------------------------

class OkTransport:
    """Answers every command with STATUS_OK (canned args or zeros); logs every raw report."""

    def __init__(self, canned=None, only_txn=None):
        self.canned = canned or {}
        self.only_txn = only_txn
        self.raw = []

    def query(self, report: bytes) -> RazerResponse:
        self.raw.append(bytes(report))
        req = parse_report(report)
        if self.only_txn is not None and report[1] != self.only_txn:
            raise RazerError("no answer")
        args = self.canned.get((req.cmd_class, req.cmd_id), bytes(80))
        return RazerResponse(STATUS_OK, req.cmd_class, req.cmd_id, len(args), args)


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeEvents:
    """Scripted event source: each poll returns the next batch and advances the clock."""

    def __init__(self, nodes, batches, clock):
        self.nodes = list(nodes)
        self.batches = list(batches)
        self.clock = clock
        self.closed = False
        self.timeouts = []

    def poll(self, timeout):
        self.timeouts.append(timeout)
        self.clock.t += timeout
        return self.batches.pop(0) if self.batches else []

    def close(self):
        self.closed = True


class Rig:
    def __init__(self, tmp_path, canned=None, only_txn=None, batches=(), node_allowed=None):
        self.root = build_sysfs(tmp_path)
        self.clock = FakeClock()
        self.canned, self.only_txn = canned, only_txn
        self.batches = list(batches)
        self.hidraw_opened, self.events_opened, self.transports = [], [], []
        self.sources = []
        self.helper = helper.Helper(sysfs_root=self.root, open_hidraw=self.open_hidraw,
                                    open_events=self.open_events, clock=self.clock,
                                    node_allowed=node_allowed)
        self.out = []

    def open_hidraw(self, path, pid):
        self.hidraw_opened.append((path, pid))
        t = OkTransport(self.canned, self.only_txn)
        self.transports.append(t)
        return t

    def open_events(self, nodes, pid):
        self.events_opened.append((list(nodes), pid))
        src = FakeEvents(nodes, self.batches, self.clock)
        self.sources.append(src)
        return src

    def send(self, req, poll_input=None):
        """Send one request; returns (responses, keep_running)."""
        self.out = []
        line = req if isinstance(req, str) else json.dumps(req)
        keep = self.helper.handle(line, self.out.append, poll_input)
        return self.out, keep

    def one(self, req):
        out, _ = self.send(req)
        assert len(out) == 1, out
        return out[0]

    def all_raw(self):
        return [r for t in self.transports for r in t.raw]


def press(code, value=1):
    return (E.EV_KEY, code, value)


# -- keylogger filter (keyfilter.py, Ruling R-1) -------------------------------------------

def test_standard_keys_cover_typing_keys():
    required = [getattr(E, f"KEY_{c}") for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"]
    required += [E.KEY_MINUS, E.KEY_EQUAL, E.KEY_LEFTBRACE, E.KEY_RIGHTBRACE, E.KEY_SEMICOLON,
                 E.KEY_APOSTROPHE, E.KEY_GRAVE, E.KEY_BACKSLASH, E.KEY_COMMA, E.KEY_DOT, E.KEY_SLASH,
                 E.KEY_102ND, E.KEY_SPACE, E.KEY_ENTER, E.KEY_TAB, E.KEY_BACKSPACE,
                 E.KEY_LEFTSHIFT, E.KEY_RIGHTSHIFT, E.KEY_LEFTCTRL, E.KEY_RIGHTCTRL, E.KEY_LEFTALT,
                 E.KEY_RIGHTALT, E.KEY_LEFTMETA, E.KEY_RIGHTMETA, E.KEY_CAPSLOCK,
                 E.KEY_UP, E.KEY_DOWN, E.KEY_LEFT, E.KEY_RIGHT, E.KEY_KP1, E.KEY_KPENTER]
    required += [getattr(E, f"KEY_F{n}") for n in range(1, 13)]
    assert all(keyfilter.classify_standard_key(c) for c in required)
    required += [E.KEY_KPLEFTPAREN, E.KEY_KPRIGHTPAREN, E.KEY_DOLLAR, E.KEY_EURO, E.KEY_NUMERIC_STAR,
                 E.KEY_NUMERIC_POUND] + [getattr(E, f"KEY_NUMERIC_{n}") for n in range(10)]
    assert all(keyfilter.classify_standard_key(c) for c in required)
    for extra in (E.KEY_F13, E.KEY_F24, E.KEY_VOLUMEUP, E.KEY_MUTE, E.KEY_MACRO1, E.BTN_SIDE, E.BTN_LEFT):
        assert not keyfilter.classify_standard_key(extra)


def _feed(f, events, source="if0:keyboard"):
    return [s for t, c, v in events if (s := f.feed(source, t, c, v)) is not None]


def test_typing_on_keyboard_yields_only_the_count():
    f = keyfilter.SignalFilter("keyboard")
    typed = []
    for ch in "hunter2":
        code = getattr(E, f"KEY_{ch.upper()}")
        typed += [press(code), press(code, 2), press(code, 0)]
    typed += [press(E.KEY_LEFTSHIFT), press(E.KEY_ENTER), press(E.KEY_SPACE), (E.EV_MSC, E.MSC_SCAN, 0x70004),
              (E.EV_SYN, 0, 0)]
    assert _feed(f, typed) == []
    assert f.standard_keys_seen == 1  # distinct sources only: no count of presses or letters


@pytest.mark.parametrize("kind", ["keypad", "unknown", "headset", None])
def test_filter_applies_to_every_non_mouse_kind(kind):
    f = keyfilter.SignalFilter(kind)
    assert _feed(f, [press(E.KEY_1), press(E.KEY_A)]) == []
    assert f.standard_keys_seen == 1


def test_mouse_side_keys_are_signals_once_each_without_values():
    f = keyfilter.SignalFilter("mouse")
    events = [press(E.KEY_1), press(E.KEY_1, 0), press(E.KEY_1), press(E.KEY_2, 2), press(E.KEY_2),
              press(E.BTN_SIDE), (E.EV_REL, E.REL_X, 5), (E.EV_REL, E.REL_X, -3), (E.EV_MSC, E.MSC_SCAN, 9)]
    assert _feed(f, events, "if1:keyboard") == [("if1:keyboard", E.EV_KEY, E.KEY_1),
                                                ("if1:keyboard", E.EV_KEY, E.KEY_2),
                                                ("if1:keyboard", E.EV_KEY, E.BTN_SIDE),
                                                ("if1:keyboard", E.EV_REL, E.REL_X)]
    assert f.standard_keys_seen == 0


def test_mouse_keyboard_node_keeps_only_the_grid():
    f = keyfilter.SignalFilter("mouse")
    events = [press(E.KEY_A), press(E.KEY_ENTER), press(E.KEY_1), press(E.KEY_0), press(E.KEY_MINUS),
              press(E.KEY_EQUAL), press(E.KEY_KP5), press(E.KEY_F13)]
    assert _feed(f, events, "if1:keyboard") == [("if1:keyboard", E.EV_KEY, c) for c in
                                                (E.KEY_1, E.KEY_0, E.KEY_MINUS, E.KEY_EQUAL, E.KEY_F13)]
    assert f.standard_keys_seen == 1
    assert _feed(f, [press(E.BTN_LEFT), press(E.KEY_A)], "if0:mouse") == [
        ("if0:mouse", E.EV_KEY, E.BTN_LEFT), ("if0:mouse", E.EV_KEY, E.KEY_A)]


def test_extra_keys_on_keyboard_are_signals():
    f = keyfilter.SignalFilter("keyboard")
    assert _feed(f, [press(E.KEY_A), press(E.KEY_MACRO1), press(E.KEY_VOLUMEUP)]) == [
        ("if0:keyboard", E.EV_KEY, E.KEY_MACRO1), ("if0:keyboard", E.EV_KEY, E.KEY_VOLUMEUP)]


# -- request whitelist ---------------------------------------------------------------------

BAD_REQUESTS = [
    "not json", "", "[]", "null", "42", '"open"', '{"op": NaN}', "{" * 3000,
    {"op": "write", "data": "00"}, {"op": "exec", "cmd": "id"}, {"op": "send", "report": "001f"},
    {"op": "OPEN", "pid": 0xA7}, {"pid": 0xA7}, {"op": None},
    {"op": "open"}, {"op": "open", "pid": "0x00a7"}, {"op": "open", "pid": True}, {"op": "open", "pid": 167.0},
    {"op": "open", "pid": -1}, {"op": "open", "pid": 0x10000},
    {"op": "open", "pid": 0x0001},                       # not connected
    {"op": "open", "pid": 0x1234},                       # connected, but not vendor 0x1532
    {"op": "open", "pid": 0xA7, "path": "/dev/hidraw4"},  # paths are never accepted
    {"op": "open", "pid": 0xA7, "hidraw": "/dev/hidraw4"},
    {"op": "open", "pid": 0xA7, "interfaces": ["/dev/hidraw4"]},
    {"op": "open", "pid": 0xA7, "interfaces": [7]},
    {"op": "open", "pid": 0xA7, "interfaces": 0},
    {"op": "open", "pid": 0xA7, "interfaces": [True]},
    {"op": "open", "pid": 0xA7, "interfaces": list(range(40))},
    {"op": "read", "name": "firmware"}, {"op": "light", "zone": "logo", "state": "green"},
    {"op": "listen", "seconds": 5}, {"op": "open", "pid": 0xA7, "id": "1"}, {"op": "open", "pid": 0xA7, "id": -1},
    {"op": "open", "pid": 0xA7, "id": True}, {"op": "stop", "id": 2**40},
]


@pytest.mark.parametrize("req", BAD_REQUESTS, ids=[str(i) for i in range(len(BAD_REQUESTS))])
def test_rejected_requests_touch_nothing(tmp_path, req):
    rig = Rig(tmp_path)
    out, keep = rig.send(req)
    assert keep is True
    assert len(out) == 1 and set(out[0]) == {"error"} and isinstance(out[0]["error"], str)
    assert rig.hidraw_opened == [] and rig.events_opened == []


def test_oversize_line_rejected(tmp_path):
    rig = Rig(tmp_path)
    out, keep = rig.send(json.dumps({"op": "open", "pid": 0xA7, "pad": "x" * 10000}))
    assert keep and set(out[0]) == {"error"} and rig.hidraw_opened == []


def test_open_uses_only_the_devices_own_nodes(tmp_path):
    rig = Rig(tmp_path)
    resp = rig.one({"op": "open", "pid": 0xA7})
    assert resp["ok"] is True and resp["kind"] == "mouse" and resp["model"] == "naga_v2_pro"
    assert resp["control"] is True
    assert rig.hidraw_opened == [("/dev/hidraw0", 0xA7)]  # control interface 0 of the Naga only
    assert "firmware" in resp["reads"] and "dpi" in resp["reads"]
    assert not any("serial" in r for r in resp["reads"])
    assert set(resp["light_zones"]) == {"logo", "scroll", "thumb"}
    assert resp["listen"] == ["if0:mouse", "if1:keyboard"]


def test_open_interface_subset_and_second_open_rejected(tmp_path):
    rig = Rig(tmp_path)
    resp = rig.one({"op": "open", "pid": 0xA7, "interfaces": [1]})
    assert resp["control"] is False and resp["reads"] == [] and resp["light_zones"] == []
    assert resp["listen"] == ["if1:keyboard"] and rig.hidraw_opened == []
    assert set(rig.one({"op": "open", "pid": 0x0227})) == {"error"}
    assert rig.hidraw_opened == []


def test_read_table_and_rejections(tmp_path):
    rig = Rig(tmp_path, canned={(0x00, 0x81): bytes([1, 2])})
    rig.one({"op": "open", "pid": 0xA7})
    assert rig.one({"op": "read", "name": "firmware"}) == {
        "ok": True, "name": "firmware", "status": "ok", "value": "v1.2", "raw": "0102"}
    for name in ("serial", "get_serial", "serial_present", "set_dpi", "raw", "brightness:nope", "", 5, None):
        assert set(rig.one({"op": "read", "name": name})) == {"error"}
    assert set(rig.one({"op": "read", "name": "firmware", "txn": 0x3F})) == {"error"}
    assert all(parse_report(r).cmd_id != 0x82 for r in rig.all_raw())


def test_read_failure_is_reported_not_raised(tmp_path):
    rig = Rig(tmp_path, only_txn=0x00)  # device answers nothing
    rig.one({"op": "open", "pid": 0xA7})
    assert rig.one({"op": "read", "name": "firmware"}) == {"ok": True, "name": "firmware", "status": "failed"}


def test_unknown_pid_only_firmware_with_three_txns(tmp_path):
    rig = Rig(tmp_path, canned={(0x00, 0x81): bytes([3, 4])}, only_txn=0x3F)
    resp = rig.one({"op": "open", "pid": 0xFFFE})
    assert resp["kind"] == "unknown" and resp["model"] == "" and resp["reads"] == ["firmware"]
    assert resp["light_zones"] == []
    out = rig.one({"op": "read", "name": "firmware"})
    assert out == {"ok": True, "name": "firmware", "status": "ok", "value": "v3.4", "raw": "0304", "txn": 0x3F}
    sent = rig.all_raw()
    assert [r[1] for r in sent] == [0x1F, 0x3F]
    assert all((parse_report(r).cmd_class, parse_report(r).cmd_id) == (0x00, 0x81) for r in sent)


def test_unknown_pid_firmware_tries_all_txns_then_fails(tmp_path):
    rig = Rig(tmp_path, only_txn=0x00)
    rig.one({"op": "open", "pid": 0xFFFE})
    assert rig.one({"op": "read", "name": "firmware"})["status"] == "failed"
    assert [r[1] for r in rig.all_raw()] == [0x1F, 0x3F, 0xFF]


def test_light_mouse_nostore_green_then_off(tmp_path):
    rig = Rig(tmp_path)
    rig.one({"op": "open", "pid": 0xA7})
    assert rig.one({"op": "light", "zone": "logo", "state": "green"}) == {
        "ok": True, "zone": "logo", "state": "green", "status": "ok"}
    assert rig.one({"op": "light", "zone": "logo", "state": "off"})["status"] == "ok"
    reps = [parse_report(r) for r in rig.all_raw()]
    assert [(r.cmd_class, r.cmd_id) for r in reps] == [(0x0F, 0x02), (0x0F, 0x02)]
    assert all(r.args[0] == 0x00 for r in reps)  # NOSTORE
    assert reps[0].args[1] == 0x04 and reps[0].args[6:9] == bytes([0, 255, 0])


@pytest.mark.parametrize("req", [
    {"op": "light", "zone": "logo", "state": "red"},
    {"op": "light", "zone": "logo", "state": "green", "color": [255, 0, 0]},
    {"op": "light", "zone": "logo", "state": "green", "store": 1},
    {"op": "light", "zone": "matrix", "state": "green"},
    {"op": "light", "zone": 4, "state": "green"},
    {"op": "light", "zone": "logo"},
])
def test_light_rejections(tmp_path, req):
    rig = Rig(tmp_path)
    rig.one({"op": "open", "pid": 0xA7})
    assert set(rig.one(req)) == {"error"}
    assert rig.all_raw() == []


def test_light_keyboard_uses_db_light_test_command(tmp_path):
    rig = Rig(tmp_path)
    resp = rig.one({"op": "open", "pid": 0x0227})
    assert resp["kind"] == "keyboard" and resp["light_zones"] == ["matrix"]
    assert "serial" not in " ".join(resp["reads"])
    rig.one({"op": "light", "zone": "matrix", "state": "green"})
    rig.one({"op": "light", "zone": "matrix", "state": "off"})
    cmd, args = db.light_test_command(db.by_pid_any(0x0227).for_pid(0x0227))
    reps = [parse_report(r) for r in rig.all_raw()]
    assert [r[1] for r in rig.all_raw()] == [cmd.txn, cmd.txn]
    assert all((r.cmd_class, r.cmd_id) == (0x0F, 0x02) and r.args[0] == 0x00 for r in reps)
    assert reps[0].args[6:9] == bytes([0, 255, 0]) and reps[1].args[6:9] == bytes([0, 0, 0])


# -- every report that can reach a device -----------------------------------------------------

def _allowed(report: bytes, during_light: bool) -> bool:
    r = parse_report(report)
    key = (r.cmd_class, r.cmd_id)
    if key == (0x00, 0x82):
        return False  # serial
    if r.cmd_id & 0x80:
        return key in whitelist.GET_IDS
    return during_light and key in whitelist.LIGHT_IDS and r.args[0] == 0x00


def test_no_persisting_command_for_any_known_device():
    lights = 0
    for info in db.load_all_devices().values():
        for pid in info.pids:
            t = OkTransport()
            s = whitelist.DeviceSession(pid, t)
            for name in s.reads:
                s.read(name)
            assert all(_allowed(r, False) for r in t.raw), (info.model, name)
            n = len(t.raw)
            for zone in s.light_zones:
                lights += 1
                for state in ("green", "off"):
                    assert s.light(zone, state)["status"] == "ok"
            assert all(_allowed(r, True) for r in t.raw[n:]), (info.model, pid)
            assert len(t.raw) - n == sum(len(s.light_packets(z, st)) for z in s.light_zones
                                             for st in ("green", "off"))
    assert lights > 50  # the light test is not silently disabled everywhere


def test_mouse_without_nostore_light_gets_none():
    # diamondback_chroma: standard matrix effects carry no storage byte
    assert whitelist.DeviceSession(0x004C, OkTransport()).light_zones == []


def test_led_state_mouse_light_is_nostore_sequence():
    s = whitelist.DeviceSession(0x005B, OkTransport())  # abyssus_v2: led_state/rgb/effect
    assert "logo" in s.light_zones
    pk = [parse_report(p) for p in s.light_packets("logo", "green")]
    assert [(p.cmd_class, p.cmd_id) for p in pk] == [(0x03, 0x00), (0x03, 0x01), (0x03, 0x02)]
    assert all(p.args[0] == 0 for p in pk)


@pytest.mark.parametrize("report", [
    build_report(0x04, 0x05, bytes([1, 0x03, 0x20, 0x03, 0x20, 0, 0])),  # set_dpi
    build_report(0x00, 0x04, bytes([3, 0])),                             # set_device_mode
    build_report(0x00, 0x05, bytes([1])),                                # set_poll_rate
    build_report(0x00, 0x82, bytes(22)),                                 # get_serial
    build_report(0x0F, 0x02, bytes([1, 4, 1, 0, 0, 1, 0, 255, 0])),       # VARSTORE light
    build_report(0x0F, 0x02, bytes([0, 4, 1, 0, 0, 1, 0, 255, 0])),       # NOSTORE light outside a light op
    build_report(0x00, 0x87, bytes(2)),                                  # unknown get id
    build_report(0x04, 0x06, bytes(0x26)),                               # set_dpi_stages
])
def test_guard_blocks_before_sending(report):
    inner = OkTransport()
    g = whitelist.GuardTransport(inner)
    with pytest.raises(whitelist.BlockedCommand):
        g.query(report)
    assert inner.raw == []


def test_guard_light_mode_still_requires_nostore():
    inner = OkTransport()
    g = whitelist.GuardTransport(inner)
    with g.light_mode():
        g.query(build_report(0x0F, 0x02, bytes([0, 4, 1, 0, 0, 1, 0, 255, 0])))
        with pytest.raises(whitelist.BlockedCommand):
            g.query(build_report(0x0F, 0x02, bytes([1, 4, 1, 0, 0, 1, 0, 255, 0])))
        with pytest.raises(whitelist.BlockedCommand):
            g.query(build_report(0x0F, 0x04, bytes([0, 4, 255])))  # brightness is not a light-test packet
    assert len(inner.raw) == 1


# -- listen -----------------------------------------------------------------------------------

def test_listen_on_keyboard_streams_no_typing(tmp_path):
    typing = [("if0:keyboard", *press(getattr(E, f"KEY_{c.upper()}"))) for c in "hunter2"]
    typing += [("if0:keyboard", *press(E.KEY_ENTER)), ("if0:keyboard", *press(E.KEY_ENTER, 0)),
               ("if0:keyboard", *press(E.KEY_VOLUMEUP)), ("if0:keyboard", E.EV_MSC, E.MSC_SCAN, 0x70004)]
    rig = Rig(tmp_path, batches=[typing[:4], typing[4:]])
    rig.one({"op": "open", "pid": 0x0227})
    out, keep = rig.send({"op": "listen", "seconds": 3})
    assert keep is True
    assert out == [{"signal": ["if0:keyboard", E.EV_KEY, E.KEY_VOLUMEUP]},
                   {"done": True, "standard_keys_seen": 1}]
    assert "hunter" not in json.dumps(out)
    nodes, pid = rig.events_opened[0]
    assert pid == 0x0227 and nodes == [("/dev/input/event2", "if0:keyboard")]
    assert rig.sources[0].closed


def test_listen_on_mouse_streams_side_keys_once(tmp_path):
    batch = [("if1:keyboard", *press(E.KEY_1)), ("if1:keyboard", *press(E.KEY_1, 0)),
             ("if1:keyboard", *press(E.KEY_1)), ("if0:mouse", *press(E.BTN_SIDE)),
             ("if0:mouse", E.EV_REL, E.REL_X, 7)]
    rig = Rig(tmp_path, batches=[batch])
    rig.one({"op": "open", "pid": 0xA7})
    out, _ = rig.send({"op": "listen", "seconds": 1})
    assert out == [{"signal": ["if1:keyboard", E.EV_KEY, E.KEY_1]},
                   {"signal": ["if0:mouse", E.EV_KEY, E.BTN_SIDE]},
                   {"signal": ["if0:mouse", E.EV_REL, E.REL_X]},
                   {"done": True, "standard_keys_seen": 0}]


@pytest.mark.parametrize("seconds", [0, -1, 121, 1e9, "10", True, None, 2.5])
def test_listen_limits(tmp_path, seconds):
    rig = Rig(tmp_path)
    rig.one({"op": "open", "pid": 0xA7})
    assert set(rig.one({"op": "listen", "seconds": seconds})) == {"error"}
    assert rig.events_opened == []


def test_listen_hard_cap_120s(tmp_path):
    rig = Rig(tmp_path)
    rig.one({"op": "open", "pid": 0xA7})
    start = rig.clock.t
    out, _ = rig.send({"op": "listen", "seconds": 120})
    assert out[-1]["done"] is True
    assert rig.clock.t - start == pytest.approx(120)
    assert max(rig.sources[0].timeouts) <= 1.0


def test_listen_stop_and_eof(tmp_path):
    rig = Rig(tmp_path)
    rig.one({"op": "open", "pid": 0xA7})
    start = rig.clock.t
    lines = iter([None, json.dumps({"op": "stop"})])
    out, keep = rig.send({"op": "listen", "seconds": 60}, poll_input=lambda timeout: next(lines))
    assert keep and out == [{"done": True, "standard_keys_seen": 0}]
    assert rig.clock.t - start < 5
    out, keep = rig.send({"op": "listen", "seconds": 60}, poll_input=lambda timeout: "")
    assert keep is False and out[-1]["done"] is True
    # anything else while listening is rejected but does not stop the recording early
    lines = iter([json.dumps({"op": "read", "name": "firmware"})] + [None] * 1000)
    rig.clock.t = start
    out, keep = rig.send({"op": "listen", "seconds": 2}, poll_input=lambda timeout: next(lines))
    assert keep and set(out[0]) == {"error"} and out[-1]["done"] is True


def test_quit(tmp_path):
    rig = Rig(tmp_path)
    out, keep = rig.send({"op": "quit"})
    assert keep is False and out == [{"ok": True}]
    assert set(rig.one({"op": "quit", "now": 1})) == {"error"}


# -- request ids and the late-stop race (fix round 1) ----------------------------------------

def test_top_level_stop_is_ignored_silently(tmp_path):
    rig = Rig(tmp_path)
    rig.one({"op": "open", "pid": 0xA7})
    for req in ({"op": "stop"}, {"op": "stop", "id": 3}):
        out, keep = rig.send(req)
        assert out == [] and keep is True


def test_ids_echoed_on_every_line(tmp_path):
    rig = Rig(tmp_path, batches=[[("if0:mouse", *press(E.BTN_SIDE))]])
    assert rig.one({"op": "open", "pid": 0xA7, "id": 7})["id"] == 7
    assert rig.one({"op": "read", "name": "serial", "id": 8}) == {"error": "unknown read", "id": 8}
    assert rig.one({"op": "bogus", "id": 9}) == {"error": "unknown op", "id": 9}
    out, _ = rig.send({"op": "listen", "seconds": 1, "id": 10})
    assert [o["id"] for o in out] == [10, 10] and "signal" in out[0] and out[1]["done"] is True


def test_stale_stop_does_not_end_a_new_listen(tmp_path):
    rig = Rig(tmp_path)
    rig.one({"op": "open", "pid": 0xA7})
    start = rig.clock.t
    lines = iter([json.dumps({"op": "stop", "id": 1})] + [None] * 100)
    out, keep = rig.send({"op": "listen", "seconds": 3, "id": 2}, poll_input=lambda t: next(lines))
    assert keep and out == [{"done": True, "standard_keys_seen": 0, "id": 2}]
    assert rig.clock.t - start == pytest.approx(3)  # ran to the end
    start = rig.clock.t
    lines = iter([None, json.dumps({"op": "stop", "id": 4})])
    out, _ = rig.send({"op": "listen", "seconds": 60, "id": 4}, poll_input=lambda t: next(lines))
    assert out == [{"done": True, "standard_keys_seen": 0, "id": 4}] and rig.clock.t - start < 5


def test_error_mid_listen_carries_the_offending_id(tmp_path):
    rig = Rig(tmp_path)
    rig.one({"op": "open", "pid": 0xA7})
    lines = iter([json.dumps({"op": "read", "name": "firmware", "id": 42}), "garbage"] + [None] * 100)
    out, _ = rig.send({"op": "listen", "seconds": 1, "id": 5}, poll_input=lambda t: next(lines))
    assert out == [{"error": "listening: only stop is accepted", "id": 42},
                   {"error": "listening: only stop is accepted"},
                   {"done": True, "standard_keys_seen": 0, "id": 5}]
    assert rig.all_raw() == []  # the read inside the listen was not executed


def test_helper_backend_discards_stale_lines(tmp_path):
    rig = Rig(tmp_path, canned={(0x00, 0x81): bytes([1, 2])})
    procs = []
    b = backend.HelperBackend(spawn=lambda argv: procs.append(FakeProc(rig)) or procs[-1])
    b.start()
    b.open(0xA7)
    b.listen(1, lambda s: None)
    b.request_stop()  # listen already over: nothing is sent
    b._write({"op": "stop", "id": 2})  # a late stop that raced the end: helper stays silent
    assert procs[0].pending == []
    procs[0].pending += ['{"error":"unknown op","id":2}', '{"error":"no id"}', "not json",
                         '{"done":true,"standard_keys_seen":0,"id":1}']
    assert b.read("firmware")["value"] == "v1.2"
    b.close()


def test_in_process_request_stop_ends_listen(tmp_path):
    rig = Rig(tmp_path, batches=[[("if0:mouse", *press(E.BTN_SIDE))]])
    b = backend.InProcessBackend(helper_obj=rig.helper)
    b.open(0xA7)
    start = rig.clock.t
    assert b.listen(60, lambda s: b.request_stop())["done"] is True
    assert rig.clock.t - start < 5
    b.request_stop()  # late stop: the next listen is not cut short
    start = rig.clock.t
    b.listen(2, lambda s: None)
    assert rig.clock.t - start == pytest.approx(2)


# -- Ruling R-4: client kind only for devices unknown to the database -------------------------

def _listen_signals(rig, req, batch):
    rig.batches[:] = [batch]
    out, _ = rig.send(req)
    return [o["signal"] for o in out if "signal" in o], out[-1]


def test_listen_kind_only_for_unknown_devices(tmp_path):
    batch = [("if0:keyboard", *press(E.KEY_1)), ("if0:keyboard", *press(E.KEY_A))]
    rig = Rig(tmp_path)
    rig.one({"op": "open", "pid": 0xFFFE})  # not in the database
    sig, done = _listen_signals(rig, {"op": "listen", "seconds": 1}, batch)
    assert sig == [] and done["standard_keys_seen"] == 1  # unknown, no kind: keyboard filter
    sig, done = _listen_signals(rig, {"op": "listen", "seconds": 1, "kind": "mouse"}, batch)
    assert sig == [["if0:keyboard", E.EV_KEY, E.KEY_1]] and done["standard_keys_seen"] == 1
    assert set(rig.one({"op": "listen", "seconds": 1, "kind": "cat"})) == {"error"}
    assert set(rig.one({"op": "listen", "seconds": 1, "kind": 1})) == {"error"}
    rig2 = Rig(tmp_path / "b")
    rig2.one({"op": "open", "pid": 0x0227})  # known keyboard: the database kind wins
    sig, done = _listen_signals(rig2, {"op": "listen", "seconds": 1, "kind": "mouse"}, batch)
    assert sig == [] and done["standard_keys_seen"] == 1


def test_serve_rearms_the_alarm(tmp_path):
    rig = Rig(tmp_path)
    r, w = os.pipe()
    os.write(w, b'{"op": "bogus"}\n{"op": "quit"}\n')
    os.close(w)
    calls = []
    try:
        assert helper.serve(rig.helper, r, lambda s: None, alarm=calls.append) == 0
    finally:
        os.close(r)
    assert len(calls) >= 3 and set(calls) == {helper.ALARM_SECONDS}
    assert helper.IDLE_TIMEOUT < helper.ALARM_SECONDS <= helper.IDLE_TIMEOUT + helper.LISTEN_MAX + 60


def test_in_process_node_filter(tmp_path):
    rig = Rig(tmp_path, node_allowed=lambda node: node.path != "/dev/hidraw0")
    resp = rig.one({"op": "open", "pid": 0xA7})
    assert resp["control"] is False and resp["reads"] == [] and rig.hidraw_opened == []
    assert resp["listen"] == ["if0:mouse", "if1:keyboard"]


# -- serve loop: EOF, idle, oversize -----------------------------------------------------------

def _serve(rig, data: bytes, idle=5.0):
    r, w = os.pipe()
    os.write(w, data)
    os.close(w)
    lines = []
    try:
        code = helper.serve(rig.helper, r, lines.append, idle_timeout=idle)
    finally:
        os.close(r)
    return code, [json.loads(x) for x in lines]


def test_serve_exits_on_eof(tmp_path):
    rig = Rig(tmp_path)
    code, out = _serve(rig, b'{"op": "open", "pid": 167}\n{"op": "bogus"}\n')
    assert code == 0
    assert out[0] == {"ready": True, "protocol": helper.PROTOCOL}
    assert out[1]["ok"] is True and set(out[2]) == {"error"} and len(out) == 3


def test_serve_ignores_partial_line_at_eof(tmp_path):
    rig = Rig(tmp_path)
    code, out = _serve(rig, b'{"op": "open", "pid": 167}')
    assert code == 0 and len(out) == 1 and rig.hidraw_opened == []


def test_serve_oversize_line_then_continues(tmp_path):
    rig = Rig(tmp_path)
    big = b'{"op": "open", "pid": 167, "x": "' + b"a" * (helper.MAX_LINE * 3) + b'"}\n'
    code, out = _serve(rig, big + b'{"op": "quit"}\n')
    assert code == 0 and set(out[1]) == {"error"} and out[2] == {"ok": True}
    assert rig.hidraw_opened == []


def test_serve_idle_timeout(tmp_path):
    rig = Rig(tmp_path)
    r, w = os.pipe()  # writer stays open: no EOF, only silence
    lines = []
    try:
        code = helper.serve(rig.helper, r, lines.append, idle_timeout=0.05)
    finally:
        os.close(r)
        os.close(w)
    assert code == 0
    assert [json.loads(x) for x in lines] == [{"ready": True, "protocol": helper.PROTOCOL},
                                              {"error": "idle timeout"}]


def test_idle_timeout_default_is_ten_minutes():
    assert helper.IDLE_TIMEOUT == 600
    assert helper.LISTEN_MAX == 120


# -- node ownership (TOCTOU guard of the real openers) -------------------------------------------

def _class_link(root: Path, cls: str, name: str, target: Path, dev: str):
    _w(target / "dev", dev + "\n")
    link = root / "class" / cls / name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)


def test_node_owner_checks_sysfs(tmp_path):
    root = Path(build_sysfs(tmp_path))
    naga_h0 = next((root / "bus/usb/devices/5-1/5-1:1.0").glob("0003:*/hidraw/hidraw0"))
    other_h4 = next((root / "bus/usb/devices/5-4/5-4:1.0").glob("0003:*/hidraw/hidraw4"))
    _class_link(root, "hidraw", "hidraw0", naga_h0, "241:0")
    _class_link(root, "hidraw", "hidraw4", other_h4, "241:4")
    rdev = os.makedev(241, 0)
    assert helper.node_owner(str(root), "/dev/hidraw0") == (0x1532, 0xA7, rdev)
    assert helper.node_owner(str(root), "/dev/hidraw4") == (0x046D, 0x1234, os.makedev(241, 4))
    assert helper.node_owner(str(root), "/dev/hidraw9") is None
    outside = tmp_path.parent / (tmp_path.name + "-outside") / "hidraw7"
    _class_link(root, "hidraw", "hidraw7", outside, "241:7")  # link leaving the sysfs root
    assert helper.node_owner(str(root), "/dev/hidraw7") is None
    for bad in ("/dev/hidraw0/../sda", "/dev/sda", "/tmp/hidraw0", "/dev/input/../hidraw0", "hidraw0"):
        assert helper.node_owner(str(root), bad) is None


# -- backends -----------------------------------------------------------------------------------

def test_helper_backend_unavailable_in_source_checkout():
    ok, reason = backend.HelperBackend.availability(which=lambda n: None, exists=lambda p: True)
    assert not ok and "Password mode unavailable" in reason and "pkexec" in reason
    ok, reason = backend.HelperBackend.availability(which=lambda n: "/usr/bin/pkexec",
                                                    exists=lambda p: p != backend.HELPER_PATH)
    assert not ok and "Password mode unavailable" in reason
    ok, reason = backend.HelperBackend.availability(which=lambda n: "/usr/bin/pkexec",
                                                    exists=lambda p: p != backend.POLICY_PATH)
    assert not ok and "Password mode unavailable" in reason
    assert backend.HelperBackend.availability(which=lambda n: "/usr/bin/pkexec", exists=lambda p: True) == (True, "")


def test_choose_backend_falls_back(monkeypatch):
    monkeypatch.setattr(backend.HelperBackend, "availability",
                        staticmethod(lambda **k: (False, "Password mode unavailable: test")))
    b = backend.choose_backend(password=True)
    assert isinstance(b, backend.InProcessBackend) and b.notice == "Password mode unavailable: test"
    assert isinstance(backend.choose_backend(password=False), backend.InProcessBackend)


class FakeProc:
    """Popen stand-in running the helper in-process."""

    def __init__(self, rig):
        self.rig = rig
        self.argv = None
        self.pending = [json.dumps({"ready": True, "protocol": helper.PROTOCOL})]
        self.alive = True
        self.returncode = None
        proc = self

        class In:
            def write(self, s):
                for line in s.splitlines():
                    proc.alive = proc.alive and rig.helper.handle(
                        line, lambda d: proc.pending.append(json.dumps(d)), None)

            def flush(self):
                pass

            def close(self):
                proc.alive = False

        class Out:
            def readline(self):
                return proc.pending.pop(0) + "\n" if proc.pending else ""

        self.stdin, self.stdout = In(), Out()

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.alive = False


def test_helper_backend_speaks_the_protocol(tmp_path):
    rig = Rig(tmp_path, canned={(0x00, 0x81): bytes([1, 2])},
              batches=[[("if0:mouse", *press(E.BTN_SIDE))]])
    procs = []

    def spawn(argv):
        p = FakeProc(rig)
        p.argv = argv
        procs.append(p)
        return p

    b = backend.HelperBackend(spawn=spawn)
    b.start()
    assert procs[0].argv == ["pkexec", backend.HELPER_PATH]
    assert b.open(0xA7)["kind"] == "mouse"
    assert b.read("firmware")["value"] == "v1.2"
    assert b.light("logo", "green")["status"] == "ok"
    got = []
    final = b.listen(1, got.append)
    assert final["done"] is True and final["standard_keys_seen"] == 0
    assert got == [["if0:mouse", E.EV_KEY, E.BTN_SIDE]]
    with pytest.raises(backend.BackendError):
        b.read("serial")
    b.close()
    assert procs[0].alive is False


def test_helper_backend_auth_cancelled():
    class Dead:
        returncode = 126
        stdin = None

        class stdout:
            @staticmethod
            def readline():
                return ""

        def wait(self, timeout=None):
            return 126

    b = backend.HelperBackend(spawn=lambda argv: Dead())
    with pytest.raises(backend.BackendError, match="cancel"):
        b.start()


def test_in_process_backend_same_operations(tmp_path):
    rig = Rig(tmp_path, canned={(0x00, 0x81): bytes([1, 2])},
              batches=[[("if1:keyboard", *press(E.KEY_3))]])
    b = backend.InProcessBackend(helper_obj=rig.helper)
    assert b.open(0xA7)["control"] is True
    assert b.read("firmware")["value"] == "v1.2"
    got = []
    assert b.listen(1, got.append)["done"] is True
    assert got == [["if1:keyboard", E.EV_KEY, E.KEY_3]]
    with pytest.raises(backend.BackendError):
        b.light("logo", "blue")
    b.close()


def test_in_process_backend_only_accessible_nodes(tmp_path, monkeypatch):
    root = build_sysfs(tmp_path)
    b = backend.InProcessBackend(sysfs_root=root)
    seen = []
    monkeypatch.setattr(b._helper, "_open_hidraw", lambda path, pid: seen.append(path))
    # discover found the hidraw node not read/writable for this user -> never opened
    monkeypatch.setattr(helper, "list_razer_devices", _no_access(helper.list_razer_devices))
    assert b.open(0xA7)["control"] is False and seen == []


def _no_access(real):
    def wrapped(root="/sys"):
        devs = real(root)
        for d in devs:
            for i in d.interfaces:
                for n in i.hidraw:
                    n.readable = n.writable = False
        return devs
    return wrapped


# -- polkit policy --------------------------------------------------------------------------------

def test_polkit_policy():
    tree = ET.parse(POLICY)
    actions = tree.getroot().findall("action")
    assert [a.get("id") for a in actions] == ["io.github.razertux.record"]
    a = actions[0]
    d = a.find("defaults")
    assert d.findtext("allow_any") == "no"
    assert d.findtext("allow_inactive") == "no"
    assert d.findtext("allow_active") == "auth_admin"
    ann = {x.get("key"): x.text for x in a.findall("annotate")}
    assert ann == {"org.freedesktop.policykit.exec.path": "/usr/lib/razertux/razertux-record-helper"}
    assert backend.HELPER_PATH == "/usr/lib/razertux/razertux-record-helper"


def test_filter_wheel_direction_and_hires():
    f = SignalFilter("mouse")
    src = "if0:mouse"
    assert f.feed(src, E.EV_REL, E.REL_WHEEL, 1) == (src, E.EV_REL, E.REL_WHEEL, 1)
    assert f.feed(src, E.EV_REL, E.REL_WHEEL, 3) is None  # magnitude does not matter
    assert f.feed(src, E.EV_REL, E.REL_WHEEL, -1) == (src, E.EV_REL, E.REL_WHEEL, -1)
    assert f.feed(src, E.EV_REL, E.REL_WHEEL_HI_RES, 120) is None
    assert f.feed(src, E.EV_REL, E.REL_HWHEEL_HI_RES, -120) is None
    assert f.feed(src, E.EV_REL, E.REL_HWHEEL, 0) is None
    assert f.feed(src, E.EV_REL, E.REL_HWHEEL, -1) == (src, E.EV_REL, E.REL_HWHEEL, -1)
    assert f.feed(src, E.EV_REL, E.REL_X, 5) == (src, E.EV_REL, E.REL_X)


# -- final review fixes ---------------------------------------------------------------------------

class WrongReplyTransport(OkTransport):
    """A reply meant for somebody else on the shared control node (e.g. the daemon's battery poll)."""

    def query(self, report: bytes) -> RazerResponse:
        self.raw.append(bytes(report))
        return RazerResponse(STATUS_OK, 0x07, 0x80, 2, bytes([0, 200]))


def test_guard_rejects_a_reply_for_another_command():
    inner = WrongReplyTransport()
    g = whitelist.GuardTransport(inner)
    with pytest.raises(RazerError):
        g.query(build_report(0x00, 0x81, bytes(2)))
    assert g.responses == []  # the foreign reply is never recorded


def test_read_with_a_foreign_reply_fails(tmp_path):
    rig = Rig(tmp_path)
    rig.open_hidraw = lambda path, pid: WrongReplyTransport()
    rig.helper._open_hidraw = rig.open_hidraw
    rig.one({"op": "open", "pid": 0xA7})
    assert rig.one({"op": "read", "name": "firmware"}) == {"ok": True, "name": "firmware", "status": "failed"}


def test_guard_light_reply_must_match_too():
    class LightWrong(OkTransport):
        def query(self, report):
            self.raw.append(bytes(report))
            return RazerResponse(STATUS_OK, 0x0F, 0x84, 3, bytes(3))
    g = whitelist.GuardTransport(LightWrong())
    with g.light_mode(), pytest.raises(RazerError):
        g.query(build_report(0x0F, 0x02, bytes([0, 4, 1, 0, 0, 1, 0, 255, 0])))


def _naga_twin(rig, busport="5-6"):
    """A second Naga V2 Pro on another port (hidraw5/6, event5/6)."""
    add_device(Path(rig.root), busport, 0x00A7, [(3, [("Razer Naga V2 Pro", MOUSE_CAPS)]),
                                                 (3, [("Razer Naga V2 Pro Keyboard", GRID_CAPS)])])


def test_open_picks_the_unit_on_the_given_bus_path(tmp_path):
    rig = Rig(tmp_path)
    _naga_twin(rig)
    resp = rig.one({"op": "open", "pid": 0xA7, "bus": "5-6"})
    assert resp["ok"] is True and rig.hidraw_opened == [("/dev/hidraw5", 0xA7)]
    rig.helper.close()
    rig.hidraw_opened.clear()
    assert rig.one({"op": "open", "pid": 0xA7, "bus": "5-1"})["ok"] is True
    assert rig.hidraw_opened == [("/dev/hidraw0", 0xA7)]


def test_open_with_two_identical_units_needs_the_bus_path(tmp_path):
    rig = Rig(tmp_path)
    _naga_twin(rig)
    assert set(rig.one({"op": "open", "pid": 0xA7})) == {"error"}
    assert rig.hidraw_opened == []


@pytest.mark.parametrize("bus", ["5-9", "5-2", "../5-1", "5-1:1.0", "/sys/bus/usb/devices/5-1", "", 5, None,
                                 "5-1" + ".1" * 20])
def test_open_rejects_a_bad_or_foreign_bus_path(tmp_path, bus):
    rig = Rig(tmp_path)
    assert set(rig.one({"op": "open", "pid": 0xA7, "bus": bus})) == {"error"}  # 5-2 is the Huntsman
    assert rig.hidraw_opened == [] and rig.helper._dev is None


def test_unknown_pid_without_razer_feature_report_gets_no_reads(tmp_path):
    rig = Rig(tmp_path)
    add_device(Path(rig.root), "5-7", 0xFFFD, [(3, [("Razer Odd", MOUSE_CAPS)])])  # descriptor: 05 01 only
    resp = rig.one({"op": "open", "pid": 0xFFFD})
    assert resp["ok"] is True and resp["control"] is False and resp["reads"] == []
    assert rig.hidraw_opened == []  # not a single ioctl on it


@pytest.mark.parametrize("desc, ok", [
    (RAZER_DESCRIPTOR, True),
    (bytes([0x75, 0x08, 0x96, 0x5A, 0x00, 0xB1, 0x02]), True),           # two-byte Report Count
    (bytes([0x85, 0x01, 0x75, 0x08, 0x95, 0x5A, 0xB1, 0x02]), True),     # with a report id
    (bytes([0x75, 0x08, 0x95, 0x5A, 0x81, 0x02]), False),                # an Input report, no Feature
    (bytes([0x75, 0x08, 0x95, 0x08, 0xB1, 0x02]), False),                # Feature of 8 bytes only
    (bytes([0x75, 0x01, 0x95, 0x5A, 0xB1, 0x02]), False),                # 90 bits
    (bytes([0x75, 0x08, 0x95, 0x2D, 0xB1, 0x02, 0xB1, 0x02]), True),     # 2 x 45 bytes, same report
    (b"\x05\x01", False), (b"", False), (bytes([0x75, 0x08, 0x95]), False),  # truncated
    (bytes([0xFE, 0x02, 0x00, 0xAA, 0xBB, 0x75, 0x08, 0x95, 0x5A, 0xB1, 0x02]), True),  # long item skipped
])
def test_razer_feature_report_in_descriptor(desc, ok):
    assert whitelist.has_razer_feature_report(desc) is ok


def test_mouse_full_keyboard_node_drops_the_grid_too():
    f = keyfilter.SignalFilter("mouse", full_keyboards={"if2:keyboard"})
    events = [press(E.KEY_1), press(E.KEY_A), press(E.KEY_F13), press(E.BTN_SIDE)]
    assert _feed(f, events, "if2:keyboard") == [("if2:keyboard", E.EV_KEY, E.KEY_F13),
                                                ("if2:keyboard", E.EV_KEY, E.BTN_SIDE)]
    assert f.standard_keys_seen == 1
    assert _feed(f, [press(E.KEY_1)], "if1:keyboard") == [("if1:keyboard", E.EV_KEY, E.KEY_1)]


def test_listen_on_mouse_drops_grid_keys_of_a_node_with_letter_keys(tmp_path):
    rig = Rig(tmp_path)
    add_device(Path(rig.root), "5-8", 0x00A7, [(3, [("Razer Naga V2 Pro", MOUSE_CAPS)]),
                                               (3, [("Razer Naga V2 Pro Keyboard", KBD_CAPS)])])
    rig.batches[:] = [[("if1:keyboard", *press(E.KEY_1)), ("if1:keyboard", *press(E.KEY_F13))]]
    rig.one({"op": "open", "pid": 0xA7, "bus": "5-8"})
    out, _ = rig.send({"op": "listen", "seconds": 1})
    assert out == [{"signal": ["if1:keyboard", E.EV_KEY, E.KEY_F13]},
                   {"done": True, "standard_keys_seen": 1}]


def test_in_process_backend_passes_the_bus_path(tmp_path):
    rig = Rig(tmp_path)
    _naga_twin(rig)
    b = backend.InProcessBackend(helper_obj=rig.helper)
    assert b.open(0xA7, bus="5-6")["control"] is True
    assert rig.hidraw_opened == [("/dev/hidraw5", 0xA7)]
    assert backend._open_req(0xA7, None, "5-6") == {"op": "open", "pid": 0xA7, "bus": "5-6"}
    assert backend._open_req(0xA7, None) == {"op": "open", "pid": 0xA7}
