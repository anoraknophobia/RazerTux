import json

import pytest
from evdev import ecodes as E
from fake_daemon import FakeTransport

from naga_config.config.model import default_profile, DisabledAction, KeyAction, Macro, Profile, Settings
from naga_config.config.store import ConfigStore
from naga_config.gui.dbus_client import DaemonClient
from naga_config.gui.main_window import MainWindow
from naga_config.hw.buttons import ButtonId


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
    assert w.mouse_view.button_text(ButtonId.SIDE_1).endswith("Taste A")
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
