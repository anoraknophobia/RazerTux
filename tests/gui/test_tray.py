import pytest
from fake_daemon import DA, NAGA, FakeTransport

from razertux.gui.dbus_client import DaemonClient
from razertux.gui.tray import TrayIcon


@pytest.fixture
def tray(qapp):
    t = FakeTransport()
    c = DaemonClient(t)
    c.status()
    tr = TrayIcon(c)
    tr.infos = []
    tr._info = lambda title, text: tr.infos.append((title, text))
    tr.confirm = True
    tr._confirm = lambda title, text: tr.confirm
    return tr, t


def test_menu_when_up(tray):
    tr, t = tray
    tr.rebuild_menu()
    texts = tr.menu_texts()
    assert "Aktives Profil: Zwei" in texts and "Standard" in texts
    assert "Akku: 80 %" in texts
    assert texts[-3:] == ["Fenster öffnen", "Originalzustand wiederherstellen", "Beenden"]
    radios = {a.text(): a.isChecked() for a in tr.menu.actions()
              if a.isCheckable() and a.text() not in ("Systemfarben verwenden", "Bildschirmanzeige")}
    assert radios == {"Standard": False, "Zwei": True}


def test_menu_when_daemon_down(tray):
    tr, t = tray
    t.up = False
    tr.client.status()
    tr.rebuild_menu()
    assert tr.menu_texts()[0] == "Dienst läuft nicht"


def test_profile_click_activates(tray):
    tr, t = tray
    tr.rebuild_menu()
    next(a for a in tr.menu.actions() if a.text() == "Standard").trigger()
    assert ("ActivateProfile", (NAGA, "default")) in t.calls


def test_restore_asks_first_and_reports_failed_fields(tray):
    tr, t = tray
    tr.confirm = False
    tr.restore_original()
    assert not any(c[0] == "RestoreOriginal" for c in t.calls)
    tr.confirm = True
    tr.restore_original()
    assert any(c[0] == "RestoreOriginal" for c in t.calls)
    assert "DPI-Stufen" in tr.infos[-1][1]


def _all_restore_fields():
    """Every field name restore_backup() can report as failed."""
    from razertux.backup import restore_backup

    class Broken:
        # capabilities (RazerMouse.supports/brightness_zones): everything, every write fails
        def supports(self, feature):
            return True

        def brightness_zones(self):
            return ["logo", "scroll", "thumb"]

        def __getattr__(self, name):
            def fail(*a, **kw):
                raise OSError("weg")
            return fail
    data = {"poll_rate": 500, "dpi": [800, 800],
            "dpi_stages": {"active": 1, "stages": [[800, 800]]},
            "brightness": {"logo": 1, "scroll": 1, "thumb": 1},
            "idle_time": 300, "low_battery_threshold": 5}
    return restore_backup(Broken(), data)


def test_restore_field_names_match_restore_backup_and_are_german(tray):
    from razertux.gui.tray import _FIELD_NAMES
    fields = _all_restore_fields()
    assert set(fields) == set(_FIELD_NAMES)
    tr, t = tray
    t.restore_failed = fields
    tr.restore_original()
    shown = tr.infos[-1][1]
    for f in fields:
        assert f not in shown  # translated, never the raw field name
    for label in ("Abfragerate", "DPI-Stufen", "Helligkeit Logo", "Leerlaufzeit",
                  "Akku-Energiesparschwelle"):
        assert label in shown


def test_restore_confirmation_names_what_is_restored(tray):
    tr, t = tray
    asked = []
    tr._confirm = lambda title, text: asked.append(text) or False
    tr.restore_original()
    text = asked[0]
    for word in ("DPI", "DPI-Stufen", "Abfragerate", "Helligkeit", "Energie"):
        assert word in text
    assert "Beleuchtung" not in text and "Effekt" in text  # effects: replug the mouse


def test_battery_text_variants():
    from razertux.gui.labels import battery_text
    assert battery_text({"connected": True, "battery": 80}) == "Akku: 80 %"
    assert battery_text({"connected": True, "battery": 80, "charging": True,
                         "connection": "wireless"}) == "Akku: 80 % ⚡ lädt"
    assert battery_text({"connected": True, "battery": 100, "connection": "wired"}) == "Kabel"
    assert battery_text({"connected": True, "battery": -1}) == "Akku: –"


def test_system_colours_toggle(tray):
    tr, t = tray
    got = []
    tr.themeRequested.connect(got.append)
    tr.set_theme_checked("synapse")
    tr.rebuild_menu()
    action = next(a for a in tr.menu.actions() if a.text() == "Systemfarben verwenden")
    assert action.isCheckable() and not action.isChecked()
    action.trigger()
    assert got == ["system"]
    tr.set_theme_checked("system")
    tr.rebuild_menu()
    action = next(a for a in tr.menu.actions() if a.text() == "Systemfarben verwenden")
    assert action.isChecked()


def test_daemon_error_mid_rebuild_keeps_menu_usable(tray):
    # hardware: daemon restarting -> "Remote peer disconnected" during the rebuild;
    # the menu stayed empty for good (every later rebuild hit a deleted submenu)
    from razertux.gui.dbus_client import DaemonError
    tr, t = tray
    orig = t.call

    def failing(member, signature="", args=()):
        if member == "GetActiveProfile":
            raise DaemonError("org.freedesktop.DBus.Error.NoReply", "Remote peer disconnected")
        return orig(member, signature, args)

    t.call = failing
    tr.rebuild_menu()
    assert tr.menu_texts()[0] == "Dienst läuft nicht"
    assert tr.menu_texts()[-1] == "Beenden"
    t.call = orig
    tr.rebuild_menu()  # must not raise on the old language submenu
    tr.rebuild_menu()
    assert "Aktives Profil: Zwei" in tr.menu_texts() and tr.menu_texts()[-1] == "Beenden"


def _two_device_tray(qapp, connected=True):
    t = FakeTransport()
    t.add_second_device(connected)
    c = DaemonClient(t)
    c.status()
    tr = TrayIcon(c)
    tr.infos = []
    tr._info = lambda title, text: tr.infos.append((title, text))
    tr._confirm = lambda title, text: True
    tr.rebuild_menu()
    return tr, t


def _submenus(tr):
    return {a.text(): a.menu() for a in tr.menu.actions() if a.menu() is not None
            and a.text() in ("Razer Naga V2 Pro", "Razer DeathAdder V2")}


def test_two_devices_get_one_submenu_each(qapp):
    tr, t = _two_device_tray(qapp)
    subs = _submenus(tr)
    assert set(subs) == {"Razer Naga V2 Pro", "Razer DeathAdder V2"}
    naga_texts = [a.text() for a in subs["Razer Naga V2 Pro"].actions() if not a.isSeparator()]
    assert "Standard" in naga_texts and "Zwei" in naga_texts and "Akku: 80 %" in naga_texts
    da_texts = [a.text() for a in subs["Razer DeathAdder V2"].actions() if not a.isSeparator()]
    assert "Präzision" in da_texts and "Akku: 55 %" in da_texts and "Zwei" not in da_texts
    checked = {a.text() for a in subs["Razer DeathAdder V2"].actions() if a.isChecked()}
    assert checked == {"Präzision"}
    assert "Aktives Profil: Zwei" not in tr.menu_texts()  # not flat any more


def test_submenu_click_activates_in_that_device(qapp):
    tr, t = _two_device_tray(qapp)
    next(a for a in _submenus(tr)["Razer DeathAdder V2"].actions() if a.text() == "Standard").trigger()
    assert ("ActivateProfile", (DA, "default")) in t.calls
    assert not any(c[0] == "ActivateProfile" and c[1][0] == NAGA for c in t.calls)


def test_submenu_restore_targets_that_device(qapp):
    tr, t = _two_device_tray(qapp)
    next(a for a in _submenus(tr)["Razer DeathAdder V2"].actions()
         if a.text() == "Originalzustand wiederherstellen").trigger()
    assert ("RestoreOriginal", (DA,)) in t.calls


def test_disconnected_second_device_keeps_flat_menu(qapp):
    tr, t = _two_device_tray(qapp, connected=False)
    assert _submenus(tr) == {}
    assert "Aktives Profil: Zwei" in tr.menu_texts()


def test_only_connected_device_is_not_the_naga(qapp):
    t = FakeTransport()
    t.devices = [[NAGA, "Razer Naga V2 Pro", "", False], [DA, "Razer DeathAdder V2", "wired", True]]
    c = DaemonClient(t)
    c.status()
    tr = TrayIcon(c)
    tr._confirm = lambda title, text: True
    tr._info = lambda title, text: None
    tr.rebuild_menu()
    assert "Aktives Profil: Präzision" in tr.menu_texts()
    assert "Akku: 55 %" in tr.menu_texts()
    tr.restore_original()
    assert ("RestoreOriginal", (DA,)) in t.calls


def test_device_signals_rebuild_the_menu(qapp):
    tr, t = _two_device_tray(qapp, connected=False)
    t.devices[1][3] = True
    tr.client.handle_signal("DevicesChanged", [])
    assert set(_submenus(tr)) == {"Razer Naga V2 Pro", "Razer DeathAdder V2"}


def test_menu_shows_version_hint_for_old_daemon(qapp):
    from razertux.gui.dbus_client import VERSION_MISMATCH, DaemonUnavailable

    class Old(FakeTransport):
        def call(self, member, signature="", args=()):
            raise DaemonUnavailable(VERSION_MISMATCH)

    c = DaemonClient(Old())
    c.status()
    tr = TrayIcon(c)
    tr.rebuild_menu()
    assert tr.menu_texts()[0] == "Dienst-Version passt nicht – bitte den RazerTux-Dienst neu starten (systemctl --user restart razertuxd) oder neu anmelden"
    assert tr.menu_texts()[-1] == "Beenden"


def test_daemon_error_in_device_list_keeps_menu_usable(qapp):
    from razertux.gui.dbus_client import DaemonError
    tr, t = _two_device_tray(qapp)
    orig = t.call

    def failing(member, signature="", args=()):
        if member == "ListDevices":
            raise DaemonError("org.freedesktop.DBus.Error.NoReply", "Remote peer disconnected")
        return orig(member, signature, args)

    t.call = failing
    tr.rebuild_menu()
    assert tr.menu_texts()[0] == "Dienst läuft nicht" and tr.menu_texts()[-1] == "Beenden"
    t.call = orig
    tr.rebuild_menu()
    assert set(_submenus(tr)) == {"Razer Naga V2 Pro", "Razer DeathAdder V2"}


def test_osd_toggle_in_menu(tray):
    tr, t = tray
    seen = []
    tr.osdRequested.connect(seen.append)
    act = next(a for a in tr.menu.actions() if a.text() == "Bildschirmanzeige")
    assert act.isCheckable() and act.isChecked()
    act.trigger()
    assert seen == [False]
    tr.set_osd_checked(False)
    assert not next(a for a in tr.menu.actions() if a.text() == "Bildschirmanzeige").isChecked()


def test_daemon_vanishing_mid_rebuild_does_not_interleave_a_second_rebuild(tray):
    """Cheap fix: available(False), emitted while the menu is being built, must not
    rebuild the menu in the middle of the outer rebuild (duplicated entries)."""
    tr, t = tray
    assert tr.client.is_available
    t.up = False  # the next call inside rebuild_menu fails and emits available(False)
    tr.rebuild_menu()
    texts = tr.menu_texts()
    assert texts.count("Beenden") == 1 and texts.count("Fenster öffnen") == 1
    assert texts[0] == "Dienst läuft nicht"
