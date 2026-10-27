"""Razer keyboards (untested): picture, page, device bar pill and recorder hand-over."""
import pytest

from razertux.config.model import Profile, Settings, default_profile
from razertux.config.store import ConfigStore
from razertux.gui import keyboard_view as kv
from razertux.gui.dbus_client import DaemonClient
from razertux.gui.main_window import MainWindow
from razertux.recorder.discover import FoundDevice

from fake_daemon import FakeTransport

MINI = 0x0257  # Razer Huntsman Mini (database kind "keyboard")
NAGA = 0x00A7


def found(pid, product="Razer Keyboard", bus="3-1"):
    return FoundDevice(0x1532, pid, "0200", "Razer", product, [], bus=bus)


@pytest.mark.parametrize("name,form", [
    ("Razer BlackWidow V3", "full"), ("Razer Huntsman Mini", "60"),
    ("Razer BlackWidow V3 Tenkeyless", "tkl"), ("Razer Huntsman V2 TKL", "tkl"),
    ("Razer BlackWidow V4 75%", "75"), ("Razer Blade 15 (2021)", "75")])
def test_form_factor_from_the_name(name, form):
    assert kv.form_factor(name) == form


def test_generic_layouts_have_the_usual_key_counts():
    assert len(kv.keys_for("full")) == 104
    assert len(kv.keys_for("tkl")) == 87
    assert len(kv.keys_for("60")) == 61
    for form in kv.FORM_FACTORS:  # no two keys overlap
        keys = kv.keys_for(form)
        for i, (x, y, w, h) in enumerate(keys):
            for x2, y2, w2, h2 in keys[i + 1:]:
                assert x + w <= x2 + 1e-9 or x2 + w2 <= x + 1e-9 or y + h <= y2 + 1e-9 \
                    or y2 + h2 <= y + 1e-9, (form, (x, y), (x2, y2))


def test_svg_is_laser_line_art():
    svg = kv.keyboard_svg("tkl")
    assert svg.count("<rect") == 87 + 1 and "fill=" not in svg and "@ACC@" in svg


def test_only_keyboards_are_listed():
    devs = [found(MINI), found(NAGA, "Razer Naga V2 Pro"), found(0x0FFF, "Unknown")]
    assert [d.pid for d in kv.connected_keyboards(lambda: devs)] == [MINI]


def test_page_shows_name_size_and_records(qapp):
    page = kv.KeyboardPage()
    page.set_device(found(MINI))
    assert page.title.text() == "Razer Huntsman Mini"
    assert "1532:0257" in page.details.text() and "60 %" in page.details.text()
    assert page.picture.form == "60"
    asked = []
    page.recordRequested.connect(asked.append)
    page.record_btn.click()
    assert [d.pid for d in asked] == [MINI]


@pytest.fixture
def win(qapp, tmp_path, monkeypatch):
    store = ConfigStore(tmp_path)
    store.save_profile(default_profile())
    store.save_profile(Profile(id="p2", name="Zwei"))
    store.save_settings(Settings(active_profile="p2"))
    w = MainWindow(store, DaemonClient(FakeTransport()))
    w.keyboard_timer.stop()
    yield w
    w.close()


def test_keyboard_pill_shows_the_page_and_back(win, monkeypatch):
    from razertux.gui import main_window
    devices = [found(MINI)]
    win.list_razer_devices = lambda: devices
    win.refresh_keyboards()
    pill = win.device_bar.keyboard_pills["kbd:0257:3-1"]
    pill.click()
    assert win.stack.currentWidget() is win.keyboard_page
    assert not win.save_btn.isVisibleTo(win) and win.device_bar.banner.isHidden()
    assert win.profile_combo.isHidden() and win.header.battery_label.isHidden()
    opened = []
    monkeypatch.setattr(main_window, "open_recorder",
                        lambda parent, client, device=None: opened.append(device))
    win.keyboard_page.record_btn.click()
    assert [d.pid for d in opened] == [MINI]
    win.device_bar.pills["naga_v2_pro"].click()  # the same mouse: back to its tabs
    assert win.stack.currentWidget() is win.tabs and win.save_btn.isVisibleTo(win)
    assert not win.profile_combo.isHidden()
    assert win.model == "naga_v2_pro"


def test_unplugged_keyboard_goes_back_to_the_mouse(win):
    devices = [found(MINI)]
    win.list_razer_devices = lambda: devices
    win.refresh_keyboards()
    win.device_bar.keyboard_pills["kbd:0257:3-1"].click()
    devices.clear()
    win.refresh_keyboards()
    assert win.device_bar.keyboard_pills == {}
    assert win.stack.currentWidget() is win.tabs


def test_scan_errors_are_ignored(win):
    def boom():
        raise OSError("no /sys")
    win.list_razer_devices = boom
    win.refresh_keyboards()
    assert win.device_bar.keyboard_pills == {}


def test_recorder_preselects_the_keyboard(qapp):
    from razertux.gui import recorder_wizard as rw
    devs = [found(NAGA, "Razer Naga V2 Pro", bus="1-1"), found(MINI, bus="3-1")]
    w = rw.RecorderWizard(None, list_devices=lambda: devs)
    try:
        w.preselect(found(MINI, bus="3-1"))
        assert w.pages["choose"].selected().pid == MINI
    finally:
        w.close()


def test_add_unknown_device_button_opens_the_recorder(win, monkeypatch):
    from razertux.gui import main_window
    opened = []
    monkeypatch.setattr(main_window, "open_recorder",
                        lambda parent, client, device=None: opened.append(device))
    btn = win.header.add_device_btn
    assert btn.text() == "+  Add unknown device" and btn.objectName() == "accentButton"
    btn.click()
    assert opened == [None]
