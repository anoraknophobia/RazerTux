
import pytest
from fake_daemon import DA, NAGA, FakeTransport
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QRegion
from PySide6.QtWidgets import QWidget

from razertux.config.model import Settings
from razertux.config.store import ConfigStore
from razertux.gui.dbus_client import DaemonClient


from razertux.gui.osd import OsdController, OsdWindow


@pytest.fixture
def setup(qapp, tmp_path):
    store = ConfigStore(tmp_path)
    store.save_settings(Settings())
    t = FakeTransport()
    client = DaemonClient(t)
    client.status()
    shown = []
    ctl = OsdController(client, store)
    ctl.window.show_message = lambda value, kind, device=None: shown.append((value, kind, device))
    return ctl, client, t, shown, store


def status(dpi, connected=True):
    return {"connected": connected, "dpi": dpi}


def test_settings_osd_default_and_round_trip():
    assert Settings().osd is True
    assert Settings.from_dict({"schema": 2, "active_profiles": {}}).osd is True
    s = Settings(); s.osd = False
    assert Settings.from_dict(s.to_dict()).osd is False


def test_window_flags(qapp):
    w = OsdWindow()
    f = w.windowFlags()
    for flag in (Qt.FramelessWindowHint, Qt.WindowStaysOnTopHint, Qt.Tool, Qt.WindowDoesNotAcceptFocus):
        assert f & flag
    assert w.testAttribute(Qt.WA_TranslucentBackground)
    assert w.testAttribute(Qt.WA_ShowWithoutActivating)
    assert (w.width(), w.height()) == (220, 220)


def test_profile_change_shows_name(setup):
    ctl, client, t, shown, _ = setup
    client.profileChanged.emit(NAGA, "default")
    assert shown == [("Standard", "PROFIL", None)]


def test_same_profile_twice_is_silent(setup):
    ctl, client, t, shown, _ = setup
    client.profileChanged.emit(NAGA, "default")
    client.profileChanged.emit(NAGA, "default")
    assert len(shown) == 1


def test_dpi_change(setup):
    ctl, client, t, shown, _ = setup
    client.statusChanged.emit(NAGA, status(1800))   # first status: baseline
    assert shown == []
    client.statusChanged.emit(NAGA, status(2500))
    assert shown == [("2500", "DPI", None)]


def test_same_dpi_and_minus_one_silent(setup):
    ctl, client, t, shown, _ = setup
    client.statusChanged.emit(NAGA, status(1800))
    client.statusChanged.emit(NAGA, status(1800))
    client.statusChanged.emit(NAGA, status(-1))
    client.statusChanged.emit(NAGA, status(1800))
    assert shown == []


def test_reconnect_resets_baseline(setup):
    ctl, client, t, shown, _ = setup
    client.statusChanged.emit(NAGA, status(1800))
    client.statusChanged.emit(NAGA, status(-1, connected=False))
    client.statusChanged.emit(NAGA, status(800))     # first after reconnect
    assert shown == []
    client.statusChanged.emit(NAGA, status(1600))
    assert shown == [("1600", "DPI", None)]


def test_daemon_restart_resets_baseline(setup):
    ctl, client, t, shown, _ = setup
    client.statusChanged.emit(NAGA, status(1800))
    client.available.emit(False)
    client.available.emit(True)
    client.statusChanged.emit(NAGA, status(800))
    assert shown == []


def test_disabled_shows_nothing(setup):
    ctl, client, t, shown, store = setup
    ctl.set_enabled(False)
    assert store.load_settings().osd is False
    client.profileChanged.emit(NAGA, "default")
    client.statusChanged.emit(NAGA, status(1800))
    client.statusChanged.emit(NAGA, status(2500))
    assert shown == []
    ctl.set_enabled(True)
    assert store.load_settings().osd is True


def test_osd_off_in_settings_respected_at_start(qapp, tmp_path):
    store = ConfigStore(tmp_path)
    s = Settings(); s.osd = False
    store.save_settings(s)
    assert OsdController(DaemonClient(FakeTransport()), store).enabled is False


def test_device_name_only_with_several_devices(setup):
    ctl, client, t, shown, _ = setup
    client.profileChanged.emit(NAGA, "default")
    t.add_second_device()
    client.refresh()
    client.profileChanged.emit(DA, "default")
    assert shown[-1] == ("Standard", "PROFIL", "Razer DeathAdder V2")


def test_one_window_replaced_by_new_message(qapp):
    w = OsdWindow()
    w.show_message("1800", "DPI")
    w.show_message("Profil1", "PROFIL")
    assert w.isVisible() and w.value == "Profil1" and w.kind == "PROFIL"
    assert w.windowOpacity() == 1.0
    w.hide()


def test_long_name_is_fitted(qapp):
    w = OsdWindow()
    w.show_message("Ein sehr sehr langer Profilname hier", "PROFIL")
    assert w.value_font_width() <= 192


def test_render_screenshot(qapp, tmp_path):
    w = OsdWindow()
    for value, kind, name in (("1800", "DPI", "task-15-osd.png"),):
        w.show_message(value, kind)
        img = QImage(220, 220, QImage.Format_ARGB32_Premultiplied)
        img.fill(Qt.transparent)
        w.render(img, QPoint(), QRegion(), QWidget.DrawChildren)
        assert img.width() == 220
        # transparent corner, green-ish pixels on the border
        assert QColor.fromRgba(img.pixel(1, 1)).alpha() == 0
        greens = sum(1 for x in range(220) for y in (12, 13, 14)
                     if (c := QColor.fromRgba(img.pixel(x, y))).alpha() > 200 and c.green() > c.red() + 40)
        assert greens > 50
    w.hide()


def test_first_profile_signal_is_only_a_baseline(qapp, tmp_path):
    store = ConfigStore(tmp_path)
    t = FakeTransport()
    t.up = False                       # daemon not there when the GUI starts
    client = DaemonClient(t)
    ctl = OsdController(client, store)
    shown = []
    ctl.window.show_message = lambda *a, **k: shown.append(a)
    t.up = True
    client.profileChanged.emit(NAGA, "p2")      # first report: baseline
    assert shown == []
    client.profileChanged.emit(NAGA, "default")
    assert len(shown) == 1


def test_refresh_after_daemon_start_or_restart_is_silent(setup):
    ctl, client, t, shown, _ = setup
    client.refresh()
    client.available.emit(False)
    client.refresh()                   # daemon restarted
    assert shown == []
    t.active[NAGA] = "default"         # restarted with another active profile
    client.available.emit(False)
    client.refresh()
    assert shown == []


def test_second_device_appearing_is_silent(setup):
    ctl, client, t, shown, _ = setup
    t.add_second_device()
    client.refresh()
    assert shown == []


def test_profile_switch_with_other_dpi_shows_one_osd(setup):
    ctl, client, t, shown, _ = setup
    now = [100.0]
    ctl._now = lambda: now[0]
    client.statusChanged.emit(NAGA, status(1800))
    client.profileChanged.emit(NAGA, "default")
    now[0] += 0.5
    client.statusChanged.emit(NAGA, status(800))
    assert shown == [("Standard", "PROFIL", None)]


def test_reload_changing_dpi_is_silent(setup):
    ctl, client, t, shown, _ = setup
    now = [100.0]
    ctl._now = lambda: now[0]
    client.statusChanged.emit(NAGA, status(1800))
    client.reload()
    now[0] += 0.5
    client.statusChanged.emit(NAGA, status(800))
    assert shown == []


def test_dpi_button_after_quiet_period_shows(setup):
    ctl, client, t, shown, _ = setup
    now = [100.0]
    ctl._now = lambda: now[0]
    client.statusChanged.emit(NAGA, status(1800))
    client.profileChanged.emit(NAGA, "default")
    now[0] += 3.0
    client.statusChanged.emit(NAGA, status(2500))
    assert shown[-1] == ("2500", "DPI", None)


def test_switching_device_in_window_shows_no_osd(qapp, tmp_path):
    from fake_daemon import FakeTransport as FT
    from test_main_window import DA_STATUS

    from razertux.config.model import Profile, default_profile
    from razertux.gui.main_window import MainWindow
    store = ConfigStore(tmp_path)
    store.save_profile(default_profile())
    store.save_profile(Profile(id="p2", name="Zwei"))
    da = store.for_model(DA)
    da.save_profile(Profile(id="default", name="Standard"))
    da.save_profile(Profile(id="dpi", name="Präzision"))
    store.save_settings(Settings(active_profile="p2"))
    t = FT()
    t.add_second_device()
    t.statuses[DA] = dict(DA_STATUS)
    client = DaemonClient(t)
    w = MainWindow(store, client)
    ctl = OsdController(client, store)
    shown = []
    ctl.window.show_message = lambda *a, **k: shown.append(a)
    w.switch_device(DA)
    w.switch_device(NAGA)
    assert shown == []


def test_disabling_hides_visible_osd(setup):
    ctl, client, t, shown, _ = setup
    ctl.window.show()
    ctl.set_enabled(False)
    assert not ctl.window.isVisible()


def test_daemon_error_in_slot_does_not_escape(setup):
    from razertux.gui.dbus_client import DaemonError
    ctl, client, t, shown, _ = setup

    def boom(*a, **k):
        raise DaemonError("x", "kaputt")
    client.list_profiles = boom
    client.devices = boom
    client.profileChanged.emit(NAGA, "default")
    assert shown == [("default", "PROFIL", None)]
