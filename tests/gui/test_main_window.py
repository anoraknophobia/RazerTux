import json

import pytest
from evdev import ecodes as E
from fake_daemon import FakeTransport

from razertux.config.model import default_profile, DisabledAction, KeyAction, Macro, Profile, Settings
from razertux.config.store import ConfigStore
from razertux.gui.dbus_client import DaemonClient
from razertux.gui.main_window import MainWindow
from razertux.hw.buttons import ButtonId


@pytest.fixture
def env(qapp, tmp_path):
    store = ConfigStore(tmp_path)
    store.save_profile(default_profile())
    store.save_profile(Profile(id="p2", name="Zwei"))
    store.save_settings(Settings(active_profile="p2"))
    transport = FakeTransport()
    client = DaemonClient(transport)
    w = MainWindow(store, client)
    w.messages = []
    w._warn = lambda title, text: w.messages.append((title, text))
    w._ask_unsaved = lambda: w.answer
    w.answer = "cancel"
    yield w, store, transport
    w.close()


def test_starts_with_active_profile(env):
    w, store, _ = env
    assert w.profile.id == "p2"
    assert not w.is_dirty and "*" not in w.windowTitle()


def test_dirty_tracking_and_title(env):
    w, *_ = env
    w.set_action(ButtonId.SIDE_1, KeyAction(E.KEY_A))
    assert w.is_dirty and w.windowTitle().endswith("*")
    assert w.mouse_view.button_text(ButtonId.SIDE_1).endswith("→  A")
    w.set_action(ButtonId.SIDE_1, KeyAction(E.KEY_A))
    w.profile.standard.pop(ButtonId.SIDE_1)
    w._update_dirty()
    assert not w.is_dirty and "*" not in w.windowTitle()


def test_hypershift_layer_edits_other_dict(env):
    w, *_ = env
    w.set_layer("hypershift")
    w.set_action(ButtonId.SIDE_1, KeyAction(E.KEY_F5))
    assert w.profile.hypershift[ButtonId.SIDE_1] == KeyAction(E.KEY_F5)
    assert ButtonId.SIDE_1 not in w.profile.standard


def test_save_blocked_on_validation_error(env):
    w, store, transport = env
    w.set_action(ButtonId.LEFT, DisabledAction())  # left click unreachable
    assert w.save() is False
    assert w.messages and "Linksklick nicht erreichbar" in w.messages[0][1]
    assert store.load_profile("p2").standard == {}
    assert not any(c[0] == "Reload" for c in transport.calls)
    assert w.is_dirty


def test_save_writes_and_reloads(env):
    w, store, transport = env
    w.set_action(ButtonId.SIDE_3, KeyAction(E.KEY_Z))
    assert w.save() is True
    assert store.load_profile("p2").standard[ButtonId.SIDE_3] == KeyAction(E.KEY_Z)
    assert ("Reload", ()) in transport.calls
    assert not w.is_dirty


def test_switch_profile_with_unsaved_changes(env):
    w, store, _ = env
    w.set_action(ButtonId.SIDE_3, KeyAction(E.KEY_Z))
    w.answer = "cancel"
    assert w.select_profile("default") is False
    assert w.profile.id == "p2" and w.profile_combo.currentData() == "p2"
    w.answer = "discard"
    assert w.select_profile("default") is True
    assert w.profile.id == "default" and not w.is_dirty
    w.set_action(ButtonId.SIDE_3, KeyAction(E.KEY_Z))
    w.answer = "save"
    assert w.select_profile("p2") is True
    assert store.load_profile("default").standard[ButtonId.SIDE_3] == KeyAction(E.KEY_Z)


def test_close_with_unsaved_changes_can_be_cancelled(env):
    w, *_ = env
    w.set_action(ButtonId.SIDE_3, KeyAction(E.KEY_Z))
    w.answer = "cancel"
    assert w.request_close() is False
    w.answer = "discard"
    assert w.request_close() is True


def test_banner_follows_availability(env):
    w, _, transport = env
    w.show()
    w.client.available.emit(False)
    assert w.banner.isVisible()
    w.client.available.emit(True)
    assert not w.banner.isVisible()


def test_import_corrupt_file_shows_german_message(env, tmp_path):
    w, *_ = env
    bad = tmp_path / "kaputt.json"
    bad.write_text("{nicht json")
    w.import_profile_from(bad)
    assert w.messages and "Import" in w.messages[0][0]
    assert w.profile.id == "p2"


def test_import_valid_file_selects_it(env, tmp_path):
    w, store, _ = env
    src = tmp_path / "x.json"
    src.write_text(json.dumps(Profile(id="fremd", name="Fremd").to_dict()))
    w.import_profile_from(src)
    assert w.profile.name == "Fremd"
    assert any(i for i in range(w.profile_combo.count()) if w.profile_combo.itemText(i) == "Fremd")


def test_battery_label(env):
    w, *_ = env
    w.client.statusChanged.emit({"connected": True, "battery": 80, "charging": True, "connection": "wireless"})
    assert "80" in w.battery_label.text()
    w.client.statusChanged.emit({"connected": True, "battery": -1, "charging": False})
    assert "80" not in w.battery_label.text()


def test_config_errors_banner_and_dismiss(env):
    w, *_ = env
    w.show()
    assert not w.errors_banner.isVisible()
    w.client.statusChanged.emit({"config_errors": ["profil x kaputt"], "openrazer_loaded": False})
    assert w.errors_banner.isVisible() and "profil x kaputt" in w.errors_banner.label.text()
    w.errors_dismiss.click()
    assert not w.errors_banner.isVisible()
    w.client.statusChanged.emit({"config_errors": ["profil x kaputt"]})
    assert not w.errors_banner.isVisible()  # same error stays dismissed
    w.client.statusChanged.emit({"config_errors": ["profil x kaputt", "neu"]})
    assert w.errors_banner.isVisible()


def test_store_errors_are_shown(env):
    w, store, _ = env
    w.show()
    (store.root / "profiles" / "naga_v2_pro" / "p2.json").write_text("{kaputt")
    w.select_profile("p2", force=True)  # corrupt active profile falls back to default
    assert w.errors_banner.isVisible() and "p2.json" in w.errors_banner.label.text()


def test_openrazer_warning(env):
    w, *_ = env
    w.show()
    assert not w.openrazer_banner.isVisible()
    w.client.statusChanged.emit({"openrazer_loaded": True})
    assert w.openrazer_banner.isVisible()
    assert "OpenRazer-Treiber ist geladen" in w.openrazer_banner.label.text()
    w.client.statusChanged.emit({"openrazer_loaded": False})
    assert not w.openrazer_banner.isVisible()


# -- Task 10: tabs, power settings, theme, battery ----------------------------------

def tab_names(w):
    return [w.tabs.tabText(i) for i in range(w.tabs.count())]


def test_tab_order(env):
    w, *_ = env
    assert tab_names(w) == ["TASTEN", "LEISTUNG", "MAUSRAD", "BELEUCHTUNG", "ENERGIE", "MAKROS"]


def test_performance_edit_marks_dirty_saves_and_reverts(env):
    w, store, transport = env
    assert w.performance_tab.profile is w.profile
    w.performance_tab.set_dpi_box.setChecked(True)
    w.performance_tab.poll_buttons[500].click()
    assert w.is_dirty
    assert w.save() is True
    p = store.load_profile("p2")
    assert p.dpi_stages == [(1800, 1800)] and p.active_stage == 1 and p.poll_rate == 500
    w.performance_tab.poll_buttons[None].click()
    assert w.is_dirty
    w.revert()
    assert w.profile.poll_rate == 500 and w.performance_tab.poll_buttons[500].isChecked()


def test_lighting_tab_follows_profile_switch(env):
    from razertux.config.model import LightingZone
    w, store, _ = env
    store.save_profile(Profile(id="lit", name="Licht",
                               lighting={"logo": LightingZone("wave", (0, 0, 0), 255)}))
    w.reload_profile_list()
    w.select_profile("lit")
    assert w.lighting_tab.profile is w.profile and w.lighting_tab.enable_box.isChecked()
    w.lighting_tab.brightness.setValue(50)
    assert w.is_dirty and w.profile.lighting["logo"].brightness == 128
    w.answer = "discard"
    assert w.select_profile("p2")
    assert not w.lighting_tab.enable_box.isChecked()


def test_power_settings_dirty_and_saved_without_clobbering(env):
    w, store, transport = env
    w.power_tab.idle_box.setChecked(True)
    assert w.is_dirty
    # meanwhile the daemon switched profile and the theme was changed
    store.save_settings(Settings(active_profile="default", theme="system"))
    assert w.save() is True
    s = store.load_settings()
    assert (s.active_profile, s.theme, s.idle_time) == ("default", "system", 300)
    assert ("Reload", ()) in transport.calls and not w.is_dirty
    w.power_tab.warn_box.setChecked(False)
    w.revert()
    assert w.settings.low_battery_notify == 15 and w.power_tab.warn_box.isChecked()


def test_battery_label_wired_and_charging(env):
    w, *_ = env
    w.client.statusChanged.emit({"connected": True, "battery": 100, "charging": True,
                                 "connection": "wired"})
    assert w.battery_label.text() == "Kabel"
    w.client.statusChanged.emit({"connected": True, "battery": 42, "charging": True,
                                 "connection": "wireless"})
    assert w.battery_label.text() == "42 %" and w.battery_label.charging  # drawn bolt, no emoji
    assert w.battery_label.level == 42 and "lädt" in w.battery_label.toolTip()
    w.client.statusChanged.emit({"connected": False, "battery": -1})
    assert w.battery_label.text() == ""


def test_theme_menu_action(env):
    w, *_ = env
    got = []
    w.themeRequested.connect(got.append)
    w.set_theme_checked("synapse")
    assert not w.system_colors_action.isChecked()
    w.system_colors_action.trigger()
    assert got == ["system"]
    w.set_theme_checked("system")
    assert w.system_colors_action.isChecked()
    w.system_colors_action.trigger()
    assert got == ["system", "synapse"]


def test_main_window_stays_small():
    from pathlib import Path
    import razertux.gui.main_window as mw
    assert len(Path(mw.__file__).read_text().splitlines()) <= 400


def test_top_buttons_locked_unless_remap(env):
    w, store, _ = env
    view = w.mouse_view
    assert w.profile.remap_top_buttons is False
    assert not w.buttons_tab.remap_toggle.isChecked()
    assert view.button_text(ButtonId.TOP_1).endswith("Mausrad-Stufe weiter (Maus)")
    assert view.button_text(ButtonId.TOP_2).endswith("DPI-Stufe weiter (Maus)")
    assert not view._buttons[ButtonId.TOP_1].isEnabled()
    assert not view._buttons[ButtonId.TOP_2].isEnabled()
    assert view._buttons[ButtonId.SIDE_1].isEnabled()
    w.buttons_tab.remap_toggle.click()
    assert w.profile.remap_top_buttons is True and w.is_dirty
    assert view._buttons[ButtonId.TOP_1].isEnabled()
    assert "(Maus)" not in view.button_text(ButtonId.TOP_1)
    assert w.save() is True
    assert store.load_profile("p2").remap_top_buttons is True
    w.select_profile("default")
    assert not w.buttons_tab.remap_toggle.isChecked()
    assert not view._buttons[ButtonId.TOP_1].isEnabled()


def test_scroll_tab_status_and_names_saved(env):
    w, store, transport = env
    w.client.statusChanged.emit({"connected": True, "scroll_stage": 6, "dpi": 2500,
                                 "connection": "wired"})
    assert w.scroll_tab.active_name.text() == "Custom"
    assert w.dpi_label.text() == "2500 DPI"
    w.scroll_tab.rows[0].name_edit.setText("Fein")
    w.scroll_tab.rows[0].name_edit.textEdited.emit("Fein")
    assert w.is_dirty
    assert w.save() is True
    assert store.load_settings().scroll_stage_names == {
        1: "Fein", 2: "Standard", 3: "Distinct", 4: "Adaptive", 5: "Smooth Scroll", 6: "Custom"}
    assert not w.is_dirty
    w.client.statusChanged.emit({"connected": True, "dpi": -1})
    assert w.dpi_label.text() == ""
    w.client.statusChanged.emit({"connected": False, "dpi": 800})
    assert w.dpi_label.text() == ""
