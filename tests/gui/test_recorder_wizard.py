"""Device recorder wizard: page flow, daemon pause/resume, report contents, sending."""
import dataclasses
import json
import queue
import threading
import time

import pytest
from evdev import ecodes as E
from fake_daemon import FakeTransport
from PySide6.QtWidgets import QApplication

from razertux.devices import db
from razertux.gui import recorder_buttons as rw_buttons
from razertux.gui import recorder_wizard as rw
from razertux.gui.dbus_client import DaemonClient
from razertux.recorder.discover import EventNode, FoundDevice, FoundInterface, Node

DA_PID = next(iter(db.load_devices()["death_adder_v2"].pids))
UNKNOWN_PID = 0x0FFF  # in no database
MOUSE, KBD = "if0:mouse", "if1:keyboard"


def wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond():
        QApplication.processEvents()
        if time.monotonic() > end:
            raise AssertionError("timed out")
        time.sleep(0.002)


def pump(seconds):
    """Let queued signals and the BTN_LEFT hold timer run."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(0.005)


def device(pid, product):
    ev = EventNode("/dev/input/event7", readable=True, name=product, ev={1, 2}, key={E.BTN_LEFT})
    iface = FoundInterface(0, 3, 1, 2, b"\x05\x01", [Node("/dev/hidraw3", True, True)], [ev])
    return FoundDevice(0x1532, pid, "0200", "Razer", product, [iface], bus="3-2")


class FakeBackend:
    notice = ""

    def __init__(self, reads=("firmware",), light_zones=(), kind="mouse", model=""):
        self.answer = {"ok": True, "kind": kind, "model": model, "control": bool(reads),
                       "reads": list(reads), "light_zones": list(light_zones), "listen": [MOUSE]}
        self.calls = []
        self.signals = queue.Queue()
        self.stop = threading.Event()
        self.listening = threading.Event()
        self.std = 0

    def start(self):
        self.calls.append(("start",))

    def open(self, pid, interfaces=None, bus=None):
        self.calls.append(("open", pid))
        self.bus = bus
        return dict(self.answer)

    def read(self, name):
        self.calls.append(("read", name))
        return {"ok": True, "name": name, "status": "ok", "value": "1.2.3", "raw": "0102"}

    def light(self, zone, state):
        self.calls.append(("light", zone, state))
        return {"ok": True, "zone": zone, "state": state, "status": "ok"}

    def listen(self, seconds, on_signal, kind=None):
        self.calls.append(("listen", seconds, kind))
        self.stop.clear()
        self.listening.set()
        try:
            while not self.stop.is_set():
                try:
                    on_signal(list(self.signals.get(timeout=0.005)))
                except queue.Empty:
                    pass
        finally:
            self.listening.clear()
        return {"done": True, "standard_keys_seen": self.std}

    def request_stop(self):
        self.stop.set()

    def close(self):
        self.calls.append(("close",))


@pytest.fixture
def make(qapp):
    made = []

    def _make(dev, backend, password=(True, ""), up=True):
        t = FakeTransport()
        t.up = up
        client = DaemonClient(t)
        w = rw.RecorderWizard(client, list_devices=lambda: [dev], make_backend=lambda pw: backend,
                              password_availability=lambda: password, settle_ms=0)
        w.transport = t
        w.opened_urls = []
        w.open_url = lambda url: w.opened_urls.append(url) or True
        made.append(w)
        return w

    yield _make
    for w in made:
        w.close()


def to_page(w, name):
    while w.page_name != name:
        wait_until(w.next_btn.isEnabled)  # e.g. the reads run on the worker thread
        w.next_btn.click()
        QApplication.processEvents()


def start_recording(w, password=True):
    w.devices_list.setCurrentRow(0)
    w.next_btn.click()
    assert w.page_name == "permission"
    page = w.pages["permission"]
    (page.password_btn if password else page.nopassword_btn).click()
    wait_until(lambda: w.page_name == "device")


def pause_calls(w):
    return [c for c in w.transport.calls if c[0] in ("PauseDevice", "ResumeDevice")]


def test_known_mouse_full_flow(make, tmp_path):
    be = FakeBackend(light_zones=("logo",), model="death_adder_v2")
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    assert w.page_name == "choose"
    assert "Experimental" in w.devices_list.item(0).text()  # in the database, not verified
    start_recording(w)
    assert be.calls[:2] == [("start",), ("open", DA_PID)]
    to_page(w, "buttons")  # known mouse: no kind question, no template picker
    assert pause_calls(w) == [("PauseDevice", ("death_adder_v2",))]
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    assert be.calls[-1] == ("listen", 120, None)  # the database kind wins
    time.sleep(rw_buttons.CLICK_GUARD + 0.05)  # BTN_LEFT right after a wizard click is ignored
    steps = page.recording.steps
    assert steps[0].button.value == "left"  # clicks first, whatever the label order
    for i, step in enumerate(steps):
        assert page.indicator.text().startswith("Recording")
        if step.button.value == "dpi_down":
            page.absent_btn.click()
        else:
            be.signals.put([MOUSE, E.EV_KEY, 0x110 + i])
            wait_until(lambda i=i: page.step_index > i)
    wait_until(lambda: page.phase == "free")
    wait_until(be.listening.is_set)
    time.sleep(rw_buttons.CLICK_GUARD + 0.05)  # well after the "Not on this device" click
    be.signals.put([MOUSE, E.EV_KEY, E.BTN_0])  # an extra button
    be.signals.put([MOUSE, E.EV_KEY, 0x110])        # already known (left): not listed again
    wait_until(lambda: len(page.recording.finish()["signals"]) == 2)
    page.finish_btn.click()
    wait_until(lambda: page.phase == "done")
    assert w.next_btn.isEnabled()
    w.next_btn.click()
    assert pause_calls(w)[-1] == ("ResumeDevice", ("death_adder_v2",))
    assert w.page_name == "reads"
    wait_until(lambda: w.next_btn.isEnabled())
    assert ("read", "firmware") in be.calls
    w.next_btn.click()
    assert w.page_name == "light"
    light = w.pages["light"]
    assert not light.rows["logo"].test_btn.isEnabled()  # consent first
    light.consent.setChecked(True)
    light.rows["logo"].test_btn.click()
    wait_until(lambda: ("light", "logo", "green") in be.calls)
    light.rows["logo"].answers["yes"].click()
    wait_until(lambda: ("light", "logo", "off") in be.calls)
    w.next_btn.click()
    assert w.page_name == "review"
    w.pages["review"].notes.setPlainText("Bought in 2024")
    data = json.loads(w.pages["review"].json_view.toPlainText())
    assert data["notes"] == "Bought in 2024"
    b = data["buttons"]
    assert b["template"] == "template_ergo"
    assert b["results"]["left"] == ["if0:mouse:BTN_LEFT"] and b["results"]["dpi_down"] == "absent"
    assert b["signals"] == ["if0:mouse:BTN_0"]
    assert data["reads"]["firmware"]["value"] == "1.2.3"
    assert data["light"] == {"logo": "yes"}
    assert data["device"]["db_model"] == "death_adder_v2" and data["system"]["kernel"]
    w.next_btn.click()
    assert w.page_name == "send"
    assert not w.next_btn.isVisible() or not w.next_btn.isEnabled()


def test_unknown_keyboard_asks_kind_and_has_no_light_page(make):
    be = FakeBackend(reads=("firmware",), light_zones=(), kind="unknown")
    w = make(device(UNKNOWN_PID, "Razer Mystery"), be)
    assert "Unknown" in w.devices_list.item(0).text()
    start_recording(w)
    w.next_btn.click()
    assert w.page_name == "kind"
    assert not w.next_btn.isEnabled()  # an answer is required
    kinds = w.pages["kind"].buttons
    assert set(kinds) == {"mouse", "keyboard", "keypad", "headset", "other", "unknown"}
    kinds["keyboard"].click()
    w.next_btn.click()
    assert w.page_name == "buttons"  # no template picker for keyboards
    assert pause_calls(w) == []  # the daemon does not know the device
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    assert be.calls[-1] == ("listen", 120, "keyboard")
    assert page.phase == "free"
    for code in (E.KEY_H, E.KEY_U, E.KEY_N, E.KEY_T, E.KEY_E, E.KEY_R, E.KEY_2, E.KEY_ENTER):
        be.signals.put([KBD, E.EV_KEY, code])
    be.signals.put([KBD, E.EV_KEY, E.KEY_MACRO])
    wait_until(lambda: page.recording.finish()["signals"] == ["if1:keyboard:KEY_MACRO"])
    page.finish_btn.click()
    wait_until(lambda: page.phase == "done")
    to_page(w, "review")
    assert "light" not in w.visited
    text = w.pages["review"].json_view.toPlainText()
    data = json.loads(text)
    assert data["device"]["kind"] == "unknown" and data["device"]["user_kind"] == "keyboard"
    assert data["buttons"]["template"] == "free"
    assert data["buttons"]["standard_keys_seen"] == 1
    for letter in ("KEY_H", "KEY_U", "KEY_N", "KEY_2", "KEY_ENTER"):
        assert letter not in text


def test_unknown_mouse_picks_a_template(make):
    be = FakeBackend(kind="unknown")
    w = make(device(UNKNOWN_PID, "Razer Mystery"), be)
    start_recording(w)
    to_page(w, "kind")
    w.pages["kind"].buttons["mouse"].click()
    w.next_btn.click()
    assert w.page_name == "template"
    picker = w.pages["template"]
    assert set(picker.choice_buttons) == {"ergo", "ambi", "compact", "vertical", "mmo", "generic"}
    assert not w.next_btn.isEnabled()
    picker.choice_buttons["ambi"].click()
    w.next_btn.click()
    assert w.page_name == "buttons"
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    assert be.calls[-1] == ("listen", 120, "mouse")
    assert page.phase == "guided" and page.recording.template == "ambi"


def test_cancel_at_buttons_page_resumes_daemon(make):
    be = FakeBackend(model="death_adder_v2")
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    start_recording(w)
    to_page(w, "buttons")
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    w.cancel_btn.click()
    wait_until(lambda: not be.listening.is_set())
    assert pause_calls(w) == [("PauseDevice", ("death_adder_v2",)), ("ResumeDevice", ("death_adder_v2",))]
    wait_until(lambda: ("close",) in be.calls)
    assert not w.isVisible()


def test_back_from_buttons_page_resumes_daemon(make):
    be = FakeBackend(model="death_adder_v2")
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    start_recording(w)
    to_page(w, "buttons")
    w.back_btn.click()
    assert pause_calls(w)[-1] == ("ResumeDevice", ("death_adder_v2",))


def test_listen_error_resumes_daemon_and_allows_going_on(make):
    be = FakeBackend(model="death_adder_v2")

    def broken(seconds, on_signal, kind=None):
        raise rw.BackendError("no input nodes")

    be.listen = broken
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    start_recording(w)
    to_page(w, "buttons")
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(lambda: page.phase == "done")
    assert "no input nodes" in page.message.text()
    assert pause_calls(w)[-1] == ("ResumeDevice", ("death_adder_v2",))
    assert w.next_btn.isEnabled()


def test_auto_stop_after_120_seconds(make):
    be = FakeBackend(kind="unknown")
    w = make(device(UNKNOWN_PID, "Razer Mystery"), be)
    clock = [1000.0]
    w.session.clock = lambda: clock[0]
    start_recording(w)
    to_page(w, "kind")
    w.pages["kind"].buttons["headset"].click()
    w.next_btn.click()
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    clock[0] += 17
    page.tick()
    assert page.indicator.text() == "Recording… 01:43"
    clock[0] += 103
    wait_until(lambda: page.phase == "done")
    assert not be.listening.is_set()


def test_password_unavailable_offers_without(make):
    be = FakeBackend()
    w = make(device(DA_PID, "Razer DeathAdder V2"), be, password=(False, "Password mode unavailable: x"))
    w.devices_list.setCurrentRow(0)
    w.next_btn.click()
    page = w.pages["permission"]
    assert not page.password_btn.isEnabled()
    assert "Password mode unavailable: x" in page.reason.text()
    page.nopassword_btn.click()
    wait_until(lambda: w.page_name == "device")
    assert ("start",) not in be.calls


def test_password_start_failure_is_shown(make):
    be = FakeBackend()

    def denied():
        raise rw.BackendError("authorization cancelled or denied (pkexec exit 126)")

    be.start = denied
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    w.devices_list.setCurrentRow(0)
    w.next_btn.click()
    w.pages["permission"].password_btn.click()
    wait_until(lambda: "pkexec exit 126" in w.pages["permission"].reason.text())
    assert w.page_name == "permission" and w.pages["permission"].nopassword_btn.isEnabled()


def _to_send(w, be):
    start_recording(w)
    to_page(w, "kind")
    w.pages["kind"].buttons["other"].click()
    w.next_btn.click()
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    page.finish_btn.click()
    wait_until(lambda: page.phase == "done")
    to_page(w, "send")


def test_submit_opens_issue_url(make):
    be = FakeBackend(kind="unknown")
    w = make(device(UNKNOWN_PID, "Razer Mystery"), be)
    _to_send(w, be)
    send = w.pages["send"]
    send.submit_btn.click()
    assert len(w.opened_urls) == 1
    assert w.opened_urls[0].startswith("https://github.com/anoraknophobia/RazerTux/issues/new?")
    assert "report=" in w.opened_urls[0]


def test_submit_uses_qdesktopservices(make, monkeypatch):
    be = FakeBackend(kind="unknown")
    w = make(device(UNKNOWN_PID, "Razer Mystery"), be)
    del w.open_url  # the real hook
    opened = []
    monkeypatch.setattr(rw.QDesktopServices, "openUrl", lambda url: opened.append(url.toString()) or True)
    _to_send(w, be)
    w.pages["send"].submit_btn.click()
    assert opened and opened[0].startswith("https://github.com/")


def test_too_long_report_asks_to_attach_the_file(make, monkeypatch):
    be = FakeBackend(kind="unknown")
    w = make(device(UNKNOWN_PID, "Razer Mystery"), be)
    _to_send(w, be)
    monkeypatch.setattr(rw, "issue_url", lambda report: ("https://github.com/x", False))
    w.pages["send"].submit_btn.click()
    assert "attach" in w.pages["send"].status.text().lower()


def test_save_as_file_writes_to_home(make, fake_home):
    be = FakeBackend(kind="unknown")
    w = make(device(UNKNOWN_PID, "Razer Mystery"), be)
    _to_send(w, be)
    w.pages["send"].save_btn.click()
    path = fake_home / "razertux-report-0fff.json"
    data = json.loads(path.read_text())
    assert data["schema"] == "razertux-device-report" and data["device"]["pid"] == UNKNOWN_PID
    assert str(path) in w.pages["send"].status.text()


def test_save_uses_model_name_for_known_devices(make, fake_home):
    be = FakeBackend(model="death_adder_v2")
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    start_recording(w)
    to_page(w, "buttons")
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    page.stop_btn.click()
    wait_until(lambda: page.phase == "done")
    to_page(w, "send")
    w.pages["send"].save_btn.click()
    assert (fake_home / "razertux-report-death_adder_v2.json").is_file()


def test_device_status_texts(make):
    naga = next(iter(db.load_devices()["naga_v2_pro"].pids))
    w = make(device(naga, "Razer Naga V2 Pro"), FakeBackend())
    assert "Known" in w.devices_list.item(0).text()


def test_no_devices_found(make):
    w = make(device(DA_PID, "Razer DeathAdder V2"), FakeBackend())
    w._list_devices = lambda: []
    w.pages["choose"].refresh()
    assert w.devices_list.count() == 0
    assert not w.next_btn.isEnabled()


def test_close_event_resumes_and_closes_backend(make):
    be = FakeBackend(model="death_adder_v2")
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    start_recording(w)
    to_page(w, "buttons")
    w.close()
    assert pause_calls(w)[-1] == ("ResumeDevice", ("death_adder_v2",))
    wait_until(lambda: ("close",) in be.calls)


# -- entry points ---------------------------------------------------------------------
def test_main_window_help_menu_opens_the_wizard(qapp, monkeypatch, tmp_path):
    from razertux.config.store import ConfigStore
    from razertux.gui import main_window as mw

    opened = []
    monkeypatch.setattr(mw, "open_recorder", lambda parent, client: opened.append(client))
    w = mw.MainWindow(ConfigStore(tmp_path), DaemonClient(FakeTransport()))
    help_menu = next(a.menu() for a in w.menuBar().actions() if a.text() == "Help")
    entry = next(a for a in help_menu.actions() if a.text() == "Record device…")
    entry.trigger()
    assert opened == [w.client]


def test_tray_has_record_entry(qapp):
    from razertux.gui.tray import TrayIcon
    tr = TrayIcon(DaemonClient(FakeTransport()))
    seen = []
    tr.recordRequested.connect(lambda: seen.append(1))
    tr.rebuild_menu()
    entry = next(a for a in tr.menu.actions() if a.text() == "Record device…")
    entry.trigger()
    assert seen == [1]


def test_open_recorder_shows_one_wizard(qapp, monkeypatch):
    monkeypatch.setattr(rw, "list_razer_devices", lambda: [])
    client = DaemonClient(FakeTransport())
    a = rw.open_recorder(None, client)
    b = rw.open_recorder(None, client)
    assert a is b and a.isVisible()
    a.close()


def test_razertux_record_entry_point(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(rw, "list_razer_devices", lambda: [])
    monkeypatch.setattr(rw.RecorderWizard, "show", lambda self: shown.append(self))
    monkeypatch.setattr(rw, "_exec", lambda app: 0)
    monkeypatch.setattr(rw, "_prepare_app", lambda app: None)  # no global theme change in tests
    monkeypatch.setattr(rw, "DaemonClient", lambda: DaemonClient(FakeTransport()))
    assert rw.main([]) == 0
    assert len(shown) == 1 and isinstance(shown[0], rw.RecorderWizard)


def test_closing_on_the_light_page_switches_the_light_off(make):
    be = FakeBackend(light_zones=("logo",), model="death_adder_v2")
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    start_recording(w)
    to_page(w, "buttons")
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    page.stop_btn.click()
    wait_until(lambda: page.phase == "done")
    to_page(w, "light")
    light = w.pages["light"]
    light.consent.setChecked(True)
    light.rows["logo"].test_btn.click()
    w.close()
    wait_until(lambda: ("close",) in be.calls)
    assert be.calls[-3:] == [("light", "logo", "green"), ("light", "logo", "off"), ("close",)]


def _guided_page(make):
    be = FakeBackend(model="death_adder_v2")
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    start_recording(w)
    to_page(w, "buttons")
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    time.sleep(rw_buttons.CLICK_GUARD + 0.05)
    return w, be, page


def test_click_on_stop_with_the_recorded_mouse_is_ignored(make):
    w, be, page = _guided_page(make)
    page.stop_btn.pressed.emit()  # mouse press on the wizard's own button ...
    be.signals.put([MOUSE, E.EV_KEY, E.BTN_LEFT])  # ... arrives as BTN_LEFT
    pump(0.25)
    assert page.recording.caught == [] and page.step_index == 0
    pump(rw_buttons.CLICK_GUARD)  # later presses count again
    be.signals.put([MOUSE, E.EV_KEY, E.BTN_LEFT])
    wait_until(lambda: page.step_index == 1)


def test_left_press_seen_before_the_wizard_press_is_dropped_too(make):
    w, be, page = _guided_page(make)
    be.signals.put([MOUSE, E.EV_KEY, E.BTN_LEFT])
    wait_until(lambda: be.signals.empty())
    QApplication.processEvents()
    page.stop_btn.pressed.emit()  # Qt saw the press only after the evdev signal
    pump(rw_buttons.HOLD_MS / 1000 + 0.1)
    assert page.recording.caught == [] and page.step_index == 0


def test_click_on_done_in_free_recording_is_ignored(make):
    be = FakeBackend(kind="unknown")
    w = make(device(UNKNOWN_PID, "Razer Mystery"), be)
    start_recording(w)
    to_page(w, "kind")
    w.pages["kind"].buttons["other"].click()
    w.next_btn.click()
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    pump(rw_buttons.CLICK_GUARD)  # the Start click is over
    page.finish_btn.pressed.emit()
    be.signals.put([MOUSE, E.EV_KEY, E.BTN_LEFT])
    pump(0.25)
    assert page.recording.caught == []
    page.finish_btn.click()
    wait_until(lambda: page.phase == "done")
    assert w.session.buttons["signals"] == []
    assert "ignored" in page.intro.text()


def test_about_to_quit_resumes_daemon_and_quits_helper(make):
    be = FakeBackend(model="death_adder_v2")
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    start_recording(w)
    to_page(w, "buttons")
    w.pages["buttons"].start_btn.click()
    wait_until(be.listening.is_set)
    QApplication.instance().aboutToQuit.emit()
    assert pause_calls(w)[-1] == ("ResumeDevice", ("death_adder_v2",))
    wait_until(lambda: ("close",) in be.calls)


def test_exception_in_a_slot_resumes_daemon(make, caplog):
    w, be, page = _guided_page(make)

    def boom(*a):
        raise ValueError("kaputt")

    page.recording.record = boom
    be.signals.put([MOUSE, E.EV_KEY, E.BTN_RIGHT])
    wait_until(lambda: page.phase == "done")
    assert pause_calls(w)[-1] == ("ResumeDevice", ("death_adder_v2",))
    assert "kaputt" in page.message.text() and "kaputt" in caplog.text
    wait_until(lambda: not be.listening.is_set())


def test_exception_in_wizard_navigation_resumes_daemon(make, caplog):
    be = FakeBackend(model="death_adder_v2")
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    start_recording(w)
    to_page(w, "buttons")

    def boom():
        raise ValueError("kaputt")

    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    page.stop_btn.click()
    wait_until(lambda: page.phase == "done")
    real_leave, page.leave = page.leave, boom  # leaving the page fails before its own resume
    w.next_btn.click()
    page.leave = real_leave
    assert pause_calls(w)[-1] == ("ResumeDevice", ("death_adder_v2",))
    assert "kaputt" in caplog.text


def test_submit_and_save_use_the_reviewed_snapshot(make, fake_home, monkeypatch):
    be = FakeBackend(kind="unknown")
    w = make(device(UNKNOWN_PID, "Razer Mystery"), be)
    _to_send(w, be)
    w.back_btn.click()
    w.pages["review"].notes.setPlainText("my note")
    shown = json.loads(w.pages["review"].json_view.toPlainText())
    w.next_btn.click()
    monkeypatch.setattr(w.session, "report", lambda: (_ for _ in ()).throw(AssertionError("rebuilt")))
    sent = []
    monkeypatch.setattr(rw, "issue_url", lambda report: sent.append(report) or ("https://github.com/x", True))
    w.pages["send"].submit_btn.click()
    w.pages["send"].save_btn.click()
    assert sent == [shown]
    assert json.loads((fake_home / "razertux-report-0fff.json").read_text()) == shown


def test_razertux_record_help(capsys):
    with pytest.raises(SystemExit) as e:
        rw.main(["--help"])
    assert e.value.code == 0 and "razertux-record" in capsys.readouterr().out


def test_backend_opens_the_unit_chosen_in_the_list(make):
    be = FakeBackend(model="death_adder_v2")
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    start_recording(w)
    assert be.bus == "3-2"  # the sysfs USB device name: the helper opens exactly that unit
    assert "3-2" not in json.dumps(dataclasses.asdict(w.session.report()))  # never reported


def _da_buttons_page(make, clock):
    be = FakeBackend(model="death_adder_v2")
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    w.session.clock = lambda: clock[0]
    start_recording(w)
    to_page(w, "buttons")
    return w, be, w.pages["buttons"]


def test_start_late_on_the_page_takes_a_fresh_daemon_pause(make):
    clock = [1000.0]
    w, be, page = _da_buttons_page(make, clock)
    assert pause_calls(w) == [("PauseDevice", ("death_adder_v2",))]
    clock[0] += 250  # the user read the page for a while: the daemon's 300 s are nearly gone
    page.start_btn.click()
    wait_until(be.listening.is_set)
    assert pause_calls(w)[1:] == [("ResumeDevice", ("death_adder_v2",)), ("PauseDevice", ("death_adder_v2",))]
    assert ("GetStatus", ("death_adder_v2",)) in w.transport.calls


def test_start_right_after_entering_keeps_the_first_pause(make):
    clock = [1000.0]
    w, be, page = _da_buttons_page(make, clock)
    clock[0] += 5
    page.start_btn.click()
    wait_until(be.listening.is_set)
    assert pause_calls(w) == [("PauseDevice", ("death_adder_v2",))]


def test_recording_refreshes_the_daemon_pause_every_minute(make):
    clock = [1000.0]
    w, be, page = _da_buttons_page(make, clock)
    page.start_btn.click()
    wait_until(be.listening.is_set)
    clock[0] += rw_buttons.PAUSE_REFRESH - 1
    page.tick()
    assert len([c for c in pause_calls(w) if c[0] == "PauseDevice"]) == 1  # not yet
    for _ in range(2):
        clock[0] += rw_buttons.PAUSE_REFRESH
        page.tick()
    pauses = [c for c in pause_calls(w) if c[0] == "PauseDevice"]
    assert len(pauses) == 3  # page entry + one refresh per minute


def test_recording_stops_when_the_daemon_is_no_longer_paused(make):
    clock = [1000.0]
    w, be, page = _da_buttons_page(make, clock)
    page.start_btn.click()
    wait_until(be.listening.is_set)
    w.transport.statuses["death_adder_v2"] = {"connected": True, "paused": False}  # e.g. daemon restarted
    clock[0] += rw_buttons.PAUSE_REFRESH
    page.tick()
    wait_until(lambda: page.phase == "done")
    assert "service" in page.message.text() and "start" in page.message.text()
    wait_until(lambda: not be.listening.is_set())
    assert pause_calls(w)[-1] == ("ResumeDevice", ("death_adder_v2",))


def test_start_is_refused_when_the_daemon_does_not_pause(make):
    clock = [1000.0]
    w, be, page = _da_buttons_page(make, clock)
    w.transport.statuses["death_adder_v2"] = {"connected": True, "paused": False}
    clock[0] += 250
    page.start_btn.click()
    QApplication.processEvents()
    assert page.phase == "done" and not be.listening.is_set()
    assert ("listen", 120, None) not in be.calls
    assert "service" in page.message.text()


def _light_page(make, model="death_adder_v2"):
    be = FakeBackend(light_zones=("logo",), model=model)
    w = make(device(DA_PID, "Razer DeathAdder V2"), be)
    start_recording(w)
    to_page(w, "buttons")
    page = w.pages["buttons"]
    page.start_btn.click()
    wait_until(be.listening.is_set)
    page.stop_btn.click()
    wait_until(lambda: page.phase in ("free", "done"))
    if page.phase == "free":
        page.finish_btn.click()
    wait_until(lambda: page.phase == "done")
    to_page(w, "light")
    return w, be, w.pages["light"]


def test_light_test_text_says_how_the_lighting_comes_back(make):
    w, be, light = _light_page(make)
    text = " ".join(lab.text() for lab in light.findChildren(rw.QLabel))
    assert "unplug" in text and "restart" in text and "re-applies" in text


def test_leaving_the_light_page_after_a_test_asks_the_daemon_to_reapply(make):
    w, be, light = _light_page(make)
    light.consent.setChecked(True)
    light.rows["logo"].test_btn.click()
    wait_until(lambda: ("light", "logo", "green") in be.calls)
    reloads_before = w.transport.calls.count(("Reload", ()))
    w.next_btn.click()  # leaves without an answer: the zone is switched off, then Reload
    wait_until(lambda: w.transport.calls.count(("Reload", ())) == reloads_before + 1)
    assert ("light", "logo", "off") in be.calls


def test_no_reload_without_a_light_test(make):
    w, be, light = _light_page(make)
    w.next_btn.click()
    pump(0.05)
    assert ("Reload", ()) not in w.transport.calls
