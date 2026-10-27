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
    assert w.messages and "Left click unreachable" in w.messages[0][1]
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


def test_banner_shows_version_hint_for_old_daemon(env):
    from razertux.gui.dbus_client import VERSION_MISMATCH, DaemonUnavailable
    w, _, transport = env
    w.show()

    def old(member, signature="", args=()):
        raise DaemonUnavailable(VERSION_MISMATCH)
    transport.call = old
    assert w.client.status() == {}  # does not raise
    assert w.banner.isVisible()
    assert w.banner.label.text() == "Service version does not match – please restart the RazerTux service (systemctl --user restart razertuxd) or log in again"
    assert "systemctl --user restart razertuxd" in w.banner.label.text()  # real remedy


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
    w.client.statusChanged.emit("naga_v2_pro", {"connected": True, "battery": 80, "charging": True, "connection": "wireless"})
    assert "80" in w.battery_label.text()
    w.client.statusChanged.emit("naga_v2_pro", {"connected": True, "battery": -1, "charging": False})
    assert "80" not in w.battery_label.text()


def test_config_errors_banner_and_dismiss(env):
    w, *_ = env
    w.show()
    assert not w.errors_banner.isVisible()
    w.client.statusChanged.emit("naga_v2_pro", {"config_errors": ["profil x kaputt"], "openrazer_loaded": False})
    assert w.errors_banner.isVisible() and "profil x kaputt" in w.errors_banner.label.text()
    w.errors_dismiss.click()
    assert not w.errors_banner.isVisible()
    w.client.statusChanged.emit("naga_v2_pro", {"config_errors": ["profil x kaputt"]})
    assert not w.errors_banner.isVisible()  # same error stays dismissed
    w.client.statusChanged.emit("naga_v2_pro", {"config_errors": ["profil x kaputt", "neu"]})
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
    w.client.statusChanged.emit("naga_v2_pro", {"openrazer_loaded": True})
    assert w.openrazer_banner.isVisible()
    assert "OpenRazer driver is loaded" in w.openrazer_banner.label.text()
    w.client.statusChanged.emit("naga_v2_pro", {"openrazer_loaded": False})
    assert not w.openrazer_banner.isVisible()


# -- tabs, power settings, theme, battery ----------------------------------

def tab_names(w):
    return [w.tabs.tabText(i) for i in range(w.tabs.count())]


def test_tab_order(env):
    w, *_ = env
    assert tab_names(w) == ["CUSTOMIZE", "PERFORMANCE", "SCROLL WHEEL", "LIGHTING", "POWER", "MACROS"]


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
    w.client.statusChanged.emit("naga_v2_pro", {"connected": True, "battery": 100, "charging": True,
                                 "connection": "wired"})
    assert w.battery_label.text() == "Wired"
    w.client.statusChanged.emit("naga_v2_pro", {"connected": True, "battery": 42, "charging": True,
                                 "connection": "wireless"})
    assert w.battery_label.text() == "42 %" and w.battery_label.charging  # drawn bolt, no emoji
    assert w.battery_label.level == 42 and "charging" in w.battery_label.toolTip()
    w.client.statusChanged.emit("naga_v2_pro", {"connected": False, "battery": -1})
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
    assert view.button_text(ButtonId.TOP_1).endswith("Scroll wheel stage cycle (mouse)")
    assert view.button_text(ButtonId.TOP_2).endswith("Sensitivity stage cycle (mouse)")
    assert not view._buttons[ButtonId.TOP_1].isEnabled()
    assert not view._buttons[ButtonId.TOP_2].isEnabled()
    assert view._buttons[ButtonId.SIDE_1].isEnabled()
    w.buttons_tab.remap_toggle.click()
    assert w.profile.remap_top_buttons is True and w.is_dirty
    assert view._buttons[ButtonId.TOP_1].isEnabled()
    assert "(mouse)" not in view.button_text(ButtonId.TOP_1)
    assert w.save() is True
    assert store.load_profile("p2").remap_top_buttons is True
    w.select_profile("default")
    assert not w.buttons_tab.remap_toggle.isChecked()
    assert not view._buttons[ButtonId.TOP_1].isEnabled()


def test_scroll_tab_status(env):
    w, store, transport = env
    w.client.statusChanged.emit("naga_v2_pro", {"connected": True, "scroll_stage": 6, "dpi": 2500,
                                 "connection": "wired"})
    assert w.scroll_tab.active_name.text() == "Custom"
    assert w.dpi_label.text() == "2500 DPI"
    assert not w.is_dirty
    w.client.statusChanged.emit("naga_v2_pro", {"connected": True, "dpi": -1})
    assert w.dpi_label.text() == ""
    w.client.statusChanged.emit("naga_v2_pro", {"connected": False, "dpi": 800})
    assert w.dpi_label.text() == ""


# -- device selector, capability tabs, experimental banner, learning ----------

DA = "death_adder_v2"
DA_STATUS = {"connected": True, "experimental": True, "writable": True, "model": DA,
             "name": "Razer DeathAdder V2", "battery": -1, "connection": "wired"}


@pytest.fixture
def two(qapp, tmp_path):
    store = ConfigStore(tmp_path)
    store.save_profile(default_profile())
    store.save_profile(Profile(id="p2", name="Zwei"))
    da = store.for_model(DA)
    da.save_profile(Profile(id="default", name="Standard"))
    da.save_profile(Profile(id="dpi", name="Präzision"))
    store.save_settings(Settings(active_profile="p2"))
    transport = FakeTransport()
    transport.add_second_device()
    transport.statuses[DA] = dict(DA_STATUS)
    w = MainWindow(store, DaemonClient(transport))
    w.messages = []
    w._warn = lambda title, text: w.messages.append((title, text))
    w._ask_unsaved = lambda: w.answer
    w.answer = "cancel"
    yield w, store, transport
    w.close()


def test_device_bar_lists_both_mice_and_starts_on_the_naga(two):
    w, *_ = two
    assert w.model == "naga_v2_pro" and set(w.device_bar.pills) == {"naga_v2_pro", DA}
    assert w.device_bar.pills["naga_v2_pro"].isChecked()
    assert w.header.title.text() == "Naga V2 Pro"


def test_switching_loads_the_profiles_of_that_model(two):
    w, store, transport = two
    w.device_bar.pills[DA].click()
    assert w.model == DA and w.profile.id == "dpi"  # the daemon's active profile of the DeathAdder
    assert [w.profile_combo.itemText(i) for i in range(w.profile_combo.count())] == ["Standard", "Präzision"]
    assert w.header.title.text() == "DeathAdder V2"
    assert store.load_settings().last_model == DA
    w.performance_tab.poll_buttons[500].click()
    assert w.save() is True
    assert store.for_model(DA).load_profile("dpi").poll_rate == 500
    assert store.load_profile("p2").poll_rate is None  # the Naga's profile is untouched
    w.device_bar.pills["naga_v2_pro"].click()
    assert w.model == "naga_v2_pro" and w.profile.id == "p2"


def test_switching_with_unsaved_changes_can_be_cancelled(two):
    w, *_ = two
    w.set_action(ButtonId.SIDE_3, KeyAction(E.KEY_Z))
    w.answer = "cancel"
    w.device_bar.pills[DA].click()
    assert w.model == "naga_v2_pro" and w.device_bar.pills["naga_v2_pro"].isChecked()
    w.answer = "discard"
    w.device_bar.pills[DA].click()
    assert w.model == DA and not w.is_dirty


def test_deathadder_has_no_energy_or_wheel_tab(two):
    w, *_ = two
    w.switch_device(DA)
    assert tab_names(w) == ["CUSTOMIZE", "PERFORMANCE", "LIGHTING", "MACROS"]
    assert not w.buttons_tab.plate_combo.isVisibleTo(w.buttons_tab)
    assert set(w.lighting_tab.effect_combos) == {"all", "logo", "scroll"}
    w.switch_device("naga_v2_pro")
    assert tab_names(w) == ["CUSTOMIZE", "PERFORMANCE", "SCROLL WHEEL", "LIGHTING", "POWER", "MACROS"]
    assert set(w.lighting_tab.effect_combos) == {"all", "logo", "thumb"}


def test_experimental_banner_and_diagnostics(two):
    from PySide6.QtGui import QGuiApplication
    w, store, transport = two
    w.show()
    assert not w.device_bar.banner.isVisible()
    w.switch_device(DA)
    assert w.device_bar.banner.isVisible()
    w.device_bar.diag_btn.click()
    assert QGuiApplication.clipboard().text() == "probe ok"
    w.device_bar.remap_toggle.click()
    assert store.for_model(DA).load_device_settings().remap_enabled is True
    assert ("Reload", ()) in transport.calls


def test_status_of_the_other_mouse_is_ignored(two):
    w, *_ = two
    w.switch_device(DA)
    w.client.statusChanged.emit("naga_v2_pro", {"connected": True, "battery": 80, "connection": "wireless"})
    assert w.battery_label.text() == "Wired"  # still the DeathAdder's
    w.client.statusChanged.emit(DA, {**DA_STATUS, "dpi": 1600})
    assert w.dpi_label.text() == "1600 DPI"


def test_learning_a_button_saves_the_signal(two):
    from razertux.hw.button_maps import InputSignal

    w, store, transport = two
    w.switch_device(DA)

    def run(dialog):
        dialog.start()
        w.client.handle_signal("ButtonLearned", [DA, "mouse", 1, 0x113])
        return dialog.result()
    w._run_learn = run
    w.learn_button(ButtonId.LEARN_1)
    assert store.for_model(DA).load_device_settings().learned == {
        ButtonId.LEARN_1: InputSignal("mouse", 1, 0x113)}
    assert ("StartLearning", (DA,)) in transport.calls


def test_learning_offered_for_experimental_mice_and_small_plates(two):
    w, store, _ = two
    assert not w.learnable(ButtonId.SIDE_1)  # Naga, 12-key plate: verified
    w.buttons_tab.plate_combo.setCurrentIndex(1)  # 6 keys: unchecked
    assert w.learnable(ButtonId.SIDE_1) and not w.learnable(ButtonId.LEFT)
    w.switch_device(DA)
    assert w.learnable(ButtonId.BACK)


def test_unknown_device_status_does_not_crash(two):
    from razertux.gui.dbus_client import DaemonError
    w, _, transport = two
    orig = transport.call

    def unknown(member, signature="", args=()):
        if args and args[0] == DA:
            raise DaemonError("io.github.razertux.Error.UnknownDevice", "Unbekanntes Gerät")
        return orig(member, signature, args)
    transport.call = unknown
    w.switch_device(DA)  # no traceback
    assert w.model == DA and w.profile.id in ("default", "dpi")


def test_no_daemon_shows_the_last_used_model(qapp, tmp_path):
    store = ConfigStore(tmp_path)
    store.for_model(DA).save_profile(Profile(id="dpi", name="Präzision"))
    s = Settings()
    s.last_model = DA
    s.active_profiles[DA] = "dpi"
    store.save_settings(s)
    transport = FakeTransport()
    transport.up = False
    w = MainWindow(store, DaemonClient(transport))
    assert w.model == DA and w.profile.id == "dpi"
    assert w.header.title.text() == "DeathAdder V2"
    w.performance_tab.poll_buttons[1000].click()
    assert w.save() is True and store.for_model(DA).load_profile("dpi").poll_rate == 1000
    w.close()


def test_device_name_comes_from_the_status(env):
    w, *_ = env
    w.client.statusChanged.emit("naga_v2_pro", {"connected": True, "name": "Razer Naga V2 Pro"})
    assert w.header.title.text() == "Naga V2 Pro"
