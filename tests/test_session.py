"""One DeviceSession per connected mouse. Fakes only: no /dev, no uinput."""
import asyncio
import json
import logging

from evdev import ecodes as E
from fake_transport import FakeTransport
from test_daemon_devices import FakeDev, RecOut, UdevDev, _props, ev, naga_transport

from razertux.config.model import (
    DefaultAction, DeviceSettings, HypershiftAction, KeyAction, MacroAction, Profile, Settings,
)
from razertux.config.store import ConfigStore
from razertux.daemon.devices import DeviceWatcher
from razertux.daemon.main import Daemon
from razertux.daemon.session import DeviceSession, needs_grab
from razertux.devices.db import load_devices
from razertux.hw.button_maps import InputSignal
from razertux.hw.buttons import ButtonId

NAGA, DA = "naga_v2_pro", "death_adder_v2"
NAGA_PID, DA_PID = 0x00A7, 0x0084
DA_LINK = "/dev/input/by-id/usb-Razer_Razer_DeathAdder_V2-"


def info(model):
    return load_devices()[model]


def da_transport():
    return FakeTransport.full()  # plausible firmware/dpi/poll answers, writes echoed


def bad_probe_transport():
    t = FakeTransport.full()
    t.responses[(0x04, 0x85)] = bytes([0, 0xFF, 0xFF, 0xFF, 0xFF, 0, 0])  # DPI 65535
    return t


def writes(t):
    return [(r.cmd_class, r.cmd_id) for r in t.requests if not r.cmd_id & 0x80]


class QueueDev(FakeDev):
    """Input node whose events arrive when the test pushes them."""

    def __init__(self, path, **kw):
        super().__init__(path, **kw)
        self.queue = asyncio.Queue()
        self.grab_calls = []

    def grab(self):
        self.grabbed = True
        self.grab_calls.append("grab")

    def ungrab(self):
        self.grabbed = False
        self.ungrabbed = True
        self.grab_calls.append("ungrab")

    async def async_read_loop(self):
        while True:
            yield await self.queue.get()

    async def push(self, *events):
        for e in events:
            self.queue.put_nowait(e)
        for _ in range(5):
            await asyncio.sleep(0)


def da_profile(pid="dpi", **kw):
    base = dict(id=pid, name=pid, dpi_stages=[(1200, 1200)], active_stage=1, poll_rate=500)
    base.update(kw)
    return Profile(**base)


def remap_profile(pid="remap"):
    return Profile(id=pid, name=pid, standard={ButtonId.BACK: KeyAction(E.KEY_A)})


def make_session(tmp_path, model=DA, transport=None, **kw):
    store = ConfigStore(tmp_path)
    t = transport if transport is not None else da_transport()
    s = DeviceSession(info(model), store.for_model(model), RecOut(),
                      transport_factory=lambda path: t,
                      find_control=lambda pids: "/dev/hidraw9",
                      find_vendor=lambda pids: None, retry_delay=0, **kw)
    return s, store, t


# -- needs_grab ---------------------------------------------

def test_needs_grab_verified_mouse_always_grabbed():
    """The Naga keeps today's behaviour: grabbed while connected, whatever the profile."""
    assert needs_grab(Profile(id="e", name="e"), DeviceSettings(), info(NAGA)) is True
    assert needs_grab(None, DeviceSettings(remap_enabled=False), info(NAGA)) is True


def test_needs_grab_experimental_only_when_profile_remaps_and_remap_enabled():
    da = info(DA)
    default = Profile(id="d", name="d", standard={b: DefaultAction() for b in ButtonId})
    on, off = DeviceSettings(remap_enabled=True), DeviceSettings(remap_enabled=False)
    assert needs_grab(default, on, da) is False
    assert needs_grab(None, on, da) is False
    assert needs_grab(remap_profile(), on, da) is True
    assert needs_grab(remap_profile(), off, da) is False
    macro = Profile(id="m", name="m", hypershift={ButtonId.BACK: MacroAction("x", "once")})
    assert needs_grab(macro, on, da) is True
    hs = Profile(id="h", name="h", standard={ButtonId.FORWARD: HypershiftAction()})
    assert needs_grab(hs, on, da) is True


# -- session ------------------------------------------------------------------------

async def test_profile_without_remap_grabs_nothing(tmp_path):
    s, store, _ = make_session(tmp_path)
    store.for_model(DA).save_profile(remap_profile())
    dev = QueueDev("/dev/input/event30", product=DA_PID)
    assert s.add_input(DA_PID, "mouse", dev) is True
    await s.settle()
    assert not dev.grabbed and s.status()["grabbed"] is False
    await s.activate("remap")  # remap_enabled defaults off for experimental mice
    assert not dev.grabbed
    ds = DeviceSettings(remap_enabled=True)
    store.for_model(DA).save_device_settings(ds)
    await s.reload()
    assert dev.grabbed  # the active profile remaps a button
    await s.activate("default")
    assert not dev.grabbed and dev.grab_calls == ["grab", "ungrab"]
    await s.close()


async def test_failed_probe_makes_experimental_mouse_read_only(tmp_path, caplog):
    t = bad_probe_transport()
    s, store, _ = make_session(tmp_path, transport=t)
    store.for_model(DA).save_profile(da_profile())
    with caplog.at_level(logging.WARNING):
        s.add_input(DA_PID, "mouse", QueueDev("/dev/input/event30", product=DA_PID))
        await s.settle()
    status = s.status()
    assert status["connected"] is True and status["writable"] is False
    await s.activate("dpi")
    await s.reload()
    s._on_dpi_stage("up")
    assert writes(t) == []  # no 0x04/0x05, no poll rate, no lighting
    assert (0x04, 0x05) not in t.sent
    assert not (tmp_path / "backups" / f"{DA}.json").exists()  # backup only after a good probe
    assert "Result: FAILED" in s.diagnostics()
    assert sum("write-protected" in r.getMessage() for r in caplog.records) == 1
    await s.close()
    assert writes(t) == []


async def test_good_probe_backs_up_then_applies(tmp_path):
    t = da_transport()
    s, store, _ = make_session(tmp_path, transport=t)
    store.for_model(DA).save_profile(da_profile())
    store.save_settings(Settings(active_profiles={DA: "dpi"}))
    s.add_input(DA_PID, "mouse", QueueDev("/dev/input/event30", product=DA_PID))
    await s.settle()
    assert s.status()["writable"] is True
    backup = json.loads((tmp_path / "backups" / f"{DA}.json").read_text())
    assert backup["dpi"] == [800, 800]
    assert (0x04, 0x05) in writes(t) and (0x00, 0x05) in writes(t)
    first_write = next(i for i, r in enumerate(t.requests) if not r.cmd_id & 0x80)
    assert all(r.cmd_id & 0x80 for r in t.requests[:first_write])
    await s.close()


async def test_learning_reports_unassigned_key_and_swallows_it(tmp_path):
    learned = []
    s, store, _ = make_session(tmp_path, on_learned=learned.append)
    kbd = QueueDev("/dev/input/event31", product=DA_PID)
    s.add_input(DA_PID, "if01", kbd)
    await s.settle()
    assert not kbd.grabbed
    await s.start_learning()
    assert kbd.grabbed  # grabbed for the duration of learning
    await kbd.push(ev(E.EV_MSC, E.MSC_SCAN, 0x70068), ev(E.EV_KEY, E.KEY_F13, 1),
                   ev(E.EV_SYN, 0, 0), ev(E.EV_KEY, E.KEY_F13, 0), ev(E.EV_SYN, 0, 0))
    assert learned == [InputSignal("if01", E.EV_KEY, E.KEY_F13)]
    assert s.out.events == []  # neither press nor release reached uinput
    assert not kbd.grabbed  # previous grab state restored
    await s.start_learning()  # the same key can be learned again (nothing left swallowed)
    await kbd.push(ev(E.EV_KEY, E.KEY_F13, 1), ev(E.EV_SYN, 0, 0))
    assert len(learned) == 2 and s.out.events == []
    await s.close()


async def test_learning_ignores_assigned_buttons_and_stop_restores_grab(tmp_path):
    learned = []
    s, store, _ = make_session(tmp_path, on_learned=learned.append)
    mouse = QueueDev("/dev/input/event30", product=DA_PID)
    s.add_input(DA_PID, "mouse", mouse)
    await s.settle()
    await s.start_learning()
    await mouse.push(ev(E.EV_KEY, E.BTN_LEFT, 1), ev(E.EV_SYN, 0, 0),
                     ev(E.EV_KEY, E.BTN_LEFT, 0), ev(E.EV_SYN, 0, 0))
    assert learned == []  # BTN_LEFT is already in the map
    assert ("key", E.BTN_LEFT, 1) in s.out.events  # and still works
    await s.stop_learning()
    assert not mouse.grabbed
    await s.close()


# -- daemon: several mice --------------------------------------------------------------

class Hw:
    """One control hidraw + FakeTransport per PID."""

    def __init__(self, transports):
        self.transports = transports

    def find_control(self, pids):
        return f"/dev/hidraw-{pids[0]:04x}"

    def transport(self, path):
        return self.transports[int(path.rsplit("-", 1)[1], 16)]


def two_mouse_daemon(tmp_path, da=None):
    hw = Hw({NAGA_PID: naga_transport(), DA_PID: da or da_transport()})
    store = ConfigStore(tmp_path)
    store.for_model(NAGA).save_profile(Profile(id="game", name="Spiel",
                                               standard={ButtonId.SIDE_1: KeyAction(E.KEY_A)}))
    store.for_model(DA).save_profile(da_profile())
    store.save_settings(Settings(active_profiles={NAGA: "game", DA: "dpi"}))
    d = Daemon(store, RecOut(), transport_factory=hw.transport, find_control=hw.find_control,
               find_vendor=lambda pids: None, openrazer_loaded=lambda: False, retry_delay=0)
    return d, hw


def dpi_values(t):
    return [(r.args[1] << 8 | r.args[2]) for r in t.requests
            if (r.cmd_class, r.cmd_id) == (0x04, 0x05)]


async def test_two_mice_get_two_sessions_with_separate_profiles(tmp_path):
    d, hw = two_mouse_daemon(tmp_path)
    await d.start()
    naga = FakeDev("/dev/input/event18", product=NAGA_PID)
    da = FakeDev("/dev/input/event30", name="Razer Razer DeathAdder V2", product=DA_PID)
    assert d.on_added("mouse", naga) is True
    assert d.on_added("mouse", da) is True
    await d.settle()
    assert set(d.sessions) == {NAGA, DA}
    assert d.sessions[NAGA].profile.id == "game" and d.sessions[DA].profile.id == "dpi"
    assert dpi_values(hw.transports[DA_PID]) == [1200]
    assert 1200 not in dpi_values(hw.transports[NAGA_PID])
    assert d.sessions[NAGA].status()["connected"] and d.sessions[DA].status()["connected"]
    assert d.hw_status()["connection"] == "wired"  # D-Bus status is the Naga's
    await d.shutdown()


async def test_unplugging_deathadder_keeps_naga_grabbed(tmp_path):
    d, hw = two_mouse_daemon(tmp_path)
    await d.start()
    naga = FakeDev("/dev/input/event18", product=NAGA_PID)
    da = FakeDev("/dev/input/event30", name="Razer Razer DeathAdder V2", product=DA_PID)
    d.on_added("mouse", naga)
    d.on_added("mouse", da)
    await d.settle()
    released = []
    orig = d.sessions[NAGA].mapper.release_all

    async def spy():
        released.append(1)
        await orig()
    d.sessions[NAGA].mapper.release_all = spy
    d.on_removed("/dev/input/event30")
    await d.settle()
    assert naga.grabbed and not naga.ungrabbed and not naga.closed
    assert released == []  # the Naga's held keys are not touched
    assert d.sessions[NAGA].status()["connected"] is True
    assert d.sessions[DA].status()["connected"] is False
    await d.shutdown()


async def test_unplugged_deathadder_stays_listed_but_releases_resources(tmp_path):
    d, hw = two_mouse_daemon(tmp_path)
    await d.start()
    da = QueueDev("/dev/input/event30", name="Razer Razer DeathAdder V2", product=DA_PID)
    d.on_added("mouse", da)
    await d.settle()
    await d.sessions[DA].start_learning()
    released = []
    orig = d.sessions[DA].mapper.release_all

    async def spy():
        released.append(1)
        await orig()
    d.sessions[DA].mapper.release_all = spy
    d.on_removed("/dev/input/event30")
    await d.settle()
    session = d.sessions[DA]
    assert DA in d.sessions
    devices = d.service.list_devices()
    assert [DA, session.info.name, "", False] in devices
    assert released  # held keys let go
    assert session.router.paths == set()
    assert session.status()["learning"] is False and session.router._learn is None
    assert da.closed
    # plugging it in again works
    d.on_added("mouse", FakeDev("/dev/input/event31", name="Razer Razer DeathAdder V2",
                                product=DA_PID))
    await d.settle()
    assert d.sessions[DA] is session and session.status()["connected"] is True
    await d.shutdown()


async def test_unknown_pid_creates_no_session(tmp_path):
    d, _ = two_mouse_daemon(tmp_path)
    await d.start()
    stranger = FakeDev("/dev/input/event40", name="Razer Something", product=0x9999)
    d.on_added("mouse", stranger)
    await d.settle()
    assert set(d.sessions) == {NAGA} and not stranger.grabbed and stranger.closed
    await d.shutdown()


def test_watcher_reports_every_db_mouse_but_not_unknown_pids():
    devs = [UdevDev(_props("00", devname="/dev/input/event30", pid="0084",
                           links=DA_LINK + "event-mouse")),
            UdevDev(_props("00", devname="/dev/input/event40", pid="9999",
                           links=DA_LINK + "event-mouse"))]
    added = []
    w = DeviceWatcher(lambda s, dv: added.append((s, dv.path)), lambda p: None,
                      list_devices=lambda: devs, open_device=FakeDev)
    w.scan()
    assert added == [("mouse", "/dev/input/event30")]


async def test_deathadder_with_failed_probe_gets_no_writes_from_daemon(tmp_path):
    t = bad_probe_transport()
    d, hw = two_mouse_daemon(tmp_path, da=t)
    await d.start()
    d.on_added("mouse", FakeDev("/dev/input/event30", product=DA_PID))
    await d.settle()
    await d.sessions[DA].activate("dpi")
    await d.service.reload()
    await d.settle()
    await d.shutdown()
    assert writes(t) == []


async def test_naga_with_failed_probe_only_warns(tmp_path, caplog):
    t = naga_transport()
    t.responses[(0x04, 0x85)] = bytes([0, 0xFF, 0xFF, 0xFF, 0xFF, 0, 0])
    store = ConfigStore(tmp_path)
    store.save_profile(Profile(id="p", name="p", poll_rate=500))
    store.save_settings(Settings(active_profile="p"))
    d = Daemon(store, RecOut(), transport_factory=lambda p: t,
               find_control=lambda pids: "/dev/hidraw2", find_vendor=lambda pids: None,
               openrazer_loaded=lambda: False, retry_delay=0)
    await d.start()
    with caplog.at_level(logging.WARNING):
        d.on_added("mouse", FakeDev("/dev/input/event18"))
        await d.settle()
    assert (0x00, 0x05) in writes(t)  # still written: no lock-out of the verified mouse
    assert d.sessions[NAGA].status()["writable"] is True
    assert "Probe" in caplog.text
    await d.shutdown()


# -- fix round 1 ---------------------------------------------------------------------

async def test_failed_backup_makes_experimental_mouse_read_only(tmp_path, monkeypatch):
    from razertux.backup import BackupError
    from razertux.daemon import session as sess

    def boom(mouse, path):
        raise BackupError("kaputt")
    monkeypatch.setattr(sess, "save_backup", boom)
    t = da_transport()
    s, store, _ = make_session(tmp_path, transport=t)
    store.for_model(DA).save_profile(da_profile())
    store.save_settings(Settings(active_profiles={DA: "dpi"}))
    s.add_input(DA_PID, "mouse", QueueDev("/dev/input/event30", product=DA_PID))
    await s.settle()
    assert s.status()["connected"] is True and s.status()["writable"] is False
    await s.activate("dpi")
    await s.close()
    assert writes(t) == []


async def test_no_write_back_after_replug_with_failed_probe(tmp_path):
    good, bad = da_transport(), bad_probe_transport()
    current = [good]
    store = ConfigStore(tmp_path)
    s = DeviceSession(info(DA), store.for_model(DA), RecOut(),
                      transport_factory=lambda path: current[0],
                      find_control=lambda pids: "/dev/hidraw9",
                      find_vendor=lambda pids: None, retry_delay=0)
    store.for_model(DA).save_profile(da_profile())
    store.save_settings(Settings(active_profiles={DA: "dpi"}))
    s.add_input(DA_PID, "mouse", QueueDev("/dev/input/event30", product=DA_PID))
    await s.settle()
    assert (0x04, 0x05) in writes(good)  # persistent DPI/poll rate written
    s.remove_input("/dev/input/event30")
    await s.settle()
    current[0] = bad
    s.add_input(DA_PID, "mouse", QueueDev("/dev/input/event31", product=DA_PID))
    await s.settle()
    assert s.status()["writable"] is False
    await s.close()
    assert writes(bad) == []  # no write-back to a mouse that failed the probe


async def test_power_settings_never_reach_experimental_mice(tmp_path):
    model, pid = "basilisk_ultimate", 0x0086
    t = FakeTransport.full()
    s, store, _ = make_session(tmp_path, model=model, transport=t)
    assert s.status()["verified"] is False
    store.save_settings(Settings(idle_time=600, low_battery_threshold=10))
    s.add_input(pid, "mouse", QueueDev("/dev/input/event30", product=pid))
    await s.settle()
    assert s.status()["writable"] is True
    await s.reload()
    assert (0x07, 0x03) not in t.sent and (0x07, 0x01) not in t.sent
    await s.close()


async def test_learning_takes_only_a_press(tmp_path):
    learned = []
    s, store, _ = make_session(tmp_path, on_learned=learned.append)
    kbd = QueueDev("/dev/input/event31", product=DA_PID)
    s.add_input(DA_PID, "if01", kbd)
    await s.settle()
    await s.start_learning()
    await kbd.push(ev(E.EV_KEY, E.KEY_F14, 0), ev(E.EV_KEY, E.KEY_F14, 2), ev(E.EV_SYN, 0, 0))
    assert learned == []
    await kbd.push(ev(E.EV_KEY, E.KEY_F13, 1), ev(E.EV_SYN, 0, 0))
    assert learned == [InputSignal("if01", E.EV_KEY, E.KEY_F13)]
    await s.close()


async def test_reload_picks_up_a_plate_change(tmp_path):
    from razertux.hw.button_maps import identify
    s, store, _ = make_session(tmp_path, model=NAGA)
    ms = store.for_model(NAGA)
    ds = ms.load_device_settings()
    ds.plate = 6
    ms.save_device_settings(ds)
    await s.reload()
    assert identify(s.router._map,
                    "if02", E.EV_KEY, E.KEY_7, 1) is None
    ds.plate = 12
    ms.save_device_settings(ds)
    await s.reload()
    assert identify(s.router._map,
                    "if02", E.EV_KEY, E.KEY_7, 1) == ButtonId.SIDE_7
    await s.close()


# -- scroll options -----------------------------------------------------------

BV3, BV3_PID = "basilisk_v3", 0x0099


def scroll_transport():
    t = FakeTransport.full()
    for k in ((0x02, 0x14), (0x02, 0x16), (0x02, 0x17)):
        t.responses[k] = lambda a: a
    t.responses[(0x02, 0x94)] = bytes([1, 1])
    t.responses[(0x02, 0x96)] = bytes([1, 0])
    t.responses[(0x02, 0x97)] = bytes([1, 1])
    return t


async def _scroll_session(tmp_path, scroll, transport=None):
    pid = next(iter(info(BV3).pids))
    t = transport or scroll_transport()
    s, store, _ = make_session(tmp_path, model=BV3, transport=t)
    store.for_model(BV3).save_profile(Profile(id="default", name="default", scroll=scroll))
    store.save_settings(Settings(active_profiles={BV3: "default"}))
    s.add_input(pid, "mouse", QueueDev("/dev/input/event40", product=pid))
    await s.settle()
    return s, t


async def test_scroll_options_are_sent_only_when_set(tmp_path):
    s, t = await _scroll_session(tmp_path, {"mode": 1, "smart_reel": True})
    w = writes(t)
    assert (0x02, 0x14) in w and (0x02, 0x17) in w and (0x02, 0x16) not in w
    assert {"scroll_mode", "scroll_smart_reel"} <= s._written
    assert "scroll_acceleration" not in s._written
    await s.close()


async def test_empty_scroll_dict_writes_nothing(tmp_path):
    s, t = await _scroll_session(tmp_path, {})
    assert not {(0x02, 0x14), (0x02, 0x16), (0x02, 0x17)} & set(writes(t))
    await s.close()


async def test_scroll_options_not_sent_when_read_only(tmp_path):
    t = scroll_transport()
    t.responses[(0x04, 0x85)] = bytes([0, 0xFF, 0xFF, 0xFF, 0xFF, 0, 0])  # probe fails
    s, t = await _scroll_session(tmp_path, {"mode": 1, "acceleration": True}, t)
    assert s.status()["writable"] is False
    assert not {(0x02, 0x14), (0x02, 0x16)} & set(writes(t))
    await s.close()


# -- overlapping connects (cable + dongle re-enumerating) ------------------------

async def test_overlapping_connects_leave_exactly_one_transport_reader_monitor(tmp_path,
                                                                                monkeypatch):
    from test_daemon_devices import FakeReader

    from razertux.daemon import session as session_mod

    class ClosingTransport:
        def __init__(self, path):
            self.inner, self.closed = naga_transport(), False

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def close(self):
            self.closed = True

    monitors = []

    class FakeMonitor:
        def __init__(self, *a, **kw):
            self.running = False
            monitors.append(self)

        def poll(self):
            pass

        def start(self):
            self.running = True

        async def stop(self):
            self.running = False

    monkeypatch.setattr(session_mod, "BatteryMonitor", FakeMonitor)
    transports, readers, ready = [], [], [False]

    def factory(path):
        transports.append(ClosingTransport(path))
        return transports[-1]

    def reader(path, cb):
        readers.append(FakeReader(path, cb))
        return readers[-1]

    store = ConfigStore(tmp_path)
    s = DeviceSession(info(NAGA), store.for_model(NAGA), RecOut(), transport_factory=factory,
                      find_control=lambda pids: "/dev/hidraw2" if ready[0] else None,
                      find_vendor=lambda pids: "/dev/hidraw3", hidraw_reader=reader,
                      retry_delay=0.01)
    assert s.add_input(0xA7, "mouse", FakeDev("/dev/input/event18", product=0xA7))
    await asyncio.sleep(0.015)  # first connect is waiting for hidraw
    assert s.add_input(0xA8, "mouse", FakeDev("/dev/input/event19", product=0xA8))
    await asyncio.sleep(0.03)   # both are waiting now
    ready[0] = True
    await s.settle()

    assert [t.closed for t in transports].count(False) == 1
    assert s._transport is not None and not s._transport.closed
    assert [r.closed for r in readers if r.started].count(False) == 1
    assert [m.running for m in monitors].count(True) == 1
    assert s.status()["connected"] and s._pid == 0xA8  # the newest node wins
    await s.close()


async def test_node_removed_while_connect_waits_opens_nothing(tmp_path):
    ready, opened = [False], []
    store = ConfigStore(tmp_path)
    s = DeviceSession(info(DA), store.for_model(DA), RecOut(),
                      transport_factory=lambda p: opened.append(p) or da_transport(),
                      find_control=lambda pids: "/dev/hidraw9" if ready[0] else None,
                      find_vendor=lambda pids: None, retry_delay=0.01)
    assert s.add_input(DA_PID, "mouse", FakeDev("/dev/input/event30", product=DA_PID))
    await asyncio.sleep(0.015)
    s.remove_input("/dev/input/event30")
    await asyncio.sleep(0)
    ready[0] = True
    await s.settle()
    assert opened == [] and not s.status()["connected"]
    await s.close()


async def test_shutdown_closes_every_session_even_if_one_fails(tmp_path, caplog):
    d, hw = two_mouse_daemon(tmp_path)
    await d.start()
    assert d.on_added("mouse", FakeDev("/dev/input/event18", product=NAGA_PID))
    assert d.on_added("mouse", FakeDev("/dev/input/event30", name="Razer Razer DeathAdder V2",
                                       product=DA_PID))
    await d.settle()
    first, second = list(d.sessions.values())
    closed, out_closed = [], []

    async def boom():
        raise RuntimeError("kaputt")
    first.close = boom
    orig = second.close

    async def ok():
        closed.append(second.model)
        await orig()
    second.close = ok
    orig_out = d.out.close
    d.out.close = lambda: (out_closed.append(True), orig_out())
    with caplog.at_level(logging.ERROR):
        await d.shutdown()
    assert closed == [second.model] and out_closed == [True]
    assert "kaputt" in caplog.text


# -- pause (device recorder) ------------------------------------------------------------

async def test_pause_releases_grab_and_resume_restores_it(tmp_path):
    s, store, _ = make_session(tmp_path, model=NAGA, transport=naga_transport())
    kbd = QueueDev("/dev/input/event40", product=NAGA_PID)
    s.add_input(NAGA_PID, "mouse", kbd)
    await s.settle()
    assert kbd.grabbed  # the Naga is always grabbed
    await s.pause()
    await s.pause()  # idempotent
    assert not kbd.grabbed and s.status()["paused"] is True
    await s.resume()
    await s.resume()  # idempotent
    assert kbd.grabbed and s.status()["paused"] is False
    assert kbd.grab_calls == ["grab", "ungrab", "grab"]
    await s.close()


async def test_pause_stops_learning_and_profile_switch_stays_released(tmp_path):
    s, store, _ = make_session(tmp_path, model=NAGA, transport=naga_transport())
    store.for_model(NAGA).save_profile(remap_profile("remap"))
    dev = QueueDev("/dev/input/event40", product=NAGA_PID)
    s.add_input(NAGA_PID, "mouse", dev)
    await s.settle()
    await s.start_learning()
    await s.pause()
    assert not dev.grabbed and s.status()["learning"] is False
    await s.activate("remap")
    await s.reload()
    assert not dev.grabbed  # no re-grab while paused
    await s.resume()
    assert dev.grabbed
    await s.close()


async def test_node_added_while_paused_is_not_grabbed(tmp_path):
    s, store, _ = make_session(tmp_path, model=NAGA, transport=naga_transport())
    await s.pause()
    dev = QueueDev("/dev/input/event41", product=NAGA_PID)
    s.add_input(NAGA_PID, "mouse", dev)
    await s.settle()
    assert not dev.grabbed
    await s.resume()
    assert dev.grabbed
    await s.close()
