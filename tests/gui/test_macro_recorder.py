import time

import pytest
from evdev import ecodes as E
from fake_daemon import FakeTransport

from naga_config.config.model import (
    ButtonStep, DefaultAction, DelayStep, KeyStep, Macro, MacroAction, Profile, default_profile,
)
from naga_config.config.store import ConfigStore
from naga_config.gui.action_dialog import ActionDialog
from naga_config.gui.dbus_client import DaemonClient
from naga_config.gui.macro_recorder import MacroRecorder, apply_fixed_delay
from naga_config.gui.macro_tab import MacroTab, profiles_using
from naga_config.gui.main_window import MainWindow
from naga_config.hw.buttons import ButtonId


def test_records_key_with_pause(qapp):
    r = MacroRecorder()
    r.start(record_delays=True)
    r.key_event(E.KEY_A, True)
    time.sleep(0.05)
    r.key_event(E.KEY_A, False)
    steps = r.stop()
    assert steps[0] == KeyStep(E.KEY_A, True) and steps[2] == KeyStep(E.KEY_A, False)
    assert isinstance(steps[1], DelayStep) and abs(steps[1].ms - 50) <= 20


def test_no_delays_and_fixed_delays(qapp):
    r = MacroRecorder()
    r.start(record_delays=False)
    r.key_event(E.KEY_A, True)
    r.key_event(E.KEY_A, False)
    assert r.stop() == [KeyStep(E.KEY_A, True), KeyStep(E.KEY_A, False)]
    r.start(record_delays=True, fixed_ms=10)
    r.key_event(E.KEY_A, True)
    time.sleep(0.03)
    r.button_event(E.BTN_SIDE, True)
    assert [s for s in r.stop() if isinstance(s, DelayStep)][0] == DelayStep(10)
    steps = [KeyStep(1, True), DelayStep(77), KeyStep(1, False)]
    assert apply_fixed_delay(steps, 10)[1] == DelayStep(10)


def test_restart_resets_state_and_releases_held(qapp):
    r = MacroRecorder()
    r.start()
    r.key_event(E.KEY_A, True)
    assert r.stop()[-1] == KeyStep(E.KEY_A, False)  # still-held key released
    r.start()
    assert r.steps == []
    r.key_event(E.KEY_A, True)  # not swallowed as "already down"
    r.key_event(E.KEY_A, True)  # auto-repeat ignored
    assert r.stop() == [KeyStep(E.KEY_A, True), KeyStep(E.KEY_A, False)]


def test_cap_stops_recording_and_keeps_releases(qapp):
    r = MacroRecorder()
    hits = []
    r.limitReached.connect(lambda: hits.append(1))
    r.start(record_delays=False)
    for _ in range(400):
        r.key_event(E.KEY_A, True)
        r.key_event(E.KEY_A, False)
    steps = r.stop()
    assert len(steps) == 500 and hits and steps[-1] == KeyStep(E.KEY_A, False)
    r.start(record_delays=False, max_steps=3)
    r.key_event(E.KEY_A, True)
    r.key_event(E.KEY_B, True)
    r.key_event(E.KEY_C, True)  # would leave no room to release
    assert not r.recording  # B would leave no room for its release
    assert r.stop() == [KeyStep(E.KEY_A, True), KeyStep(E.KEY_A, False)]


@pytest.fixture
def env(qapp, tmp_path):
    store = ConfigStore(tmp_path)
    store.save_macros({"gruss": Macro("gruss", "Gruß", [KeyStep(E.KEY_H, True), KeyStep(E.KEY_H, False)])})
    p = default_profile()
    p.standard[ButtonId.SIDE_1] = MacroAction("gruss", "once", 1)
    store.save_profile(p)
    p2 = Profile(id="p2", name="Zwei")
    p2.hypershift[ButtonId.SIDE_2] = MacroAction("gruss", "once", 1)
    store.save_profile(p2)
    transport = FakeTransport()
    w = MainWindow(store, DaemonClient(transport))
    w._warn = lambda t, x: None
    w._ask_unsaved = lambda: "discard"
    w._confirm = lambda t, x: True
    w.macro_tab._confirm = lambda t, x: w.confirm_texts.append(x) or True
    w.confirm_texts = []
    yield w, store, transport
    w.close()


def test_profiles_using_lists_names(env):
    w, store, _ = env
    assert sorted(profiles_using(store.list_profiles(), "gruss")) == ["Standard", "Zwei"]
    assert profiles_using(store.list_profiles(), "nix") == []


def test_delete_used_macro_replaces_bindings(env):
    w, store, transport = env
    w.macro_tab.macro_list.setCurrentRow(0)
    assert w.macro_tab.delete_macro()
    assert "Standard" in w.confirm_texts[0] and "Zwei" in w.confirm_texts[0]
    assert store.load_macros() == {}
    assert all(not isinstance(a, MacroAction) for pr in store.list_profiles()
               for layer in (pr.standard, pr.hypershift) for a in layer.values())
    assert store.load_profile("default").standard[ButtonId.SIDE_1] == DefaultAction()
    assert ButtonId.SIDE_2 not in store.load_profile("p2").hypershift
    assert not w.is_dirty and ("Reload", ()) in transport.calls


def test_combined_unsaved_and_save(env):
    w, store, transport = env
    tab = w.macro_tab
    tab.macro_list.setCurrentRow(0)
    tab.insert_delay(25)
    assert w.is_dirty and w.windowTitle().endswith("*")
    assert store.load_macros()["gruss"].steps[-1] != DelayStep(25)  # not yet on disk
    transport.calls.clear()
    assert w.save()
    assert DelayStep(25) in store.load_macros()["gruss"].steps
    assert not w.is_dirty and ("Reload", ()) in transport.calls


def test_discard_reverts_macros(env):
    w, store, _ = env
    w.macro_tab.add_macro("Neu")
    assert w.is_dirty and len(w.macros) == 2
    w._ask_unsaved = lambda: "discard"
    assert w._resolve_unsaved()
    assert len(w.macros) == 1 and not w.is_dirty
    assert w.macro_tab.macro_list.count() == 1


def test_new_macro_visible_in_action_dialog(env):
    w, *_ = env
    mid = w.macro_tab.add_macro("Frisch")
    assert mid.startswith("frisch-")
    d = ActionDialog(w, DefaultAction(), w.macros, button=ButtonId.SIDE_3)
    assert d.macro_combo.findData(mid) >= 0


def test_tab_recording_appends_and_edit_ops(env):
    w, *_ = env
    tab = w.macro_tab
    tab.macro_list.setCurrentRow(0)
    tab.delays_check.setChecked(False)
    tab.start_recording()
    tab.recorder.key_event(E.KEY_A, True)
    tab.recorder.button_event(E.BTN_SIDE, True)
    tab.stop_recording()
    steps = tab.current.steps
    assert steps[2:] == [KeyStep(E.KEY_A, True), ButtonStep(E.BTN_SIDE, True),
                         ButtonStep(E.BTN_SIDE, False), KeyStep(E.KEY_A, False)]
    tab.insert_delay(5)
    i = next(i for i, s in enumerate(tab.current.steps) if isinstance(s, DelayStep))
    tab.set_delay(i, 99999)
    assert tab.current.steps[i] == DelayStep(60000)
    tab.fixed_spin.setValue(7)
    tab.apply_fixed_to_all()
    assert tab.current.steps[i] == DelayStep(7)
    tab.step_list.setCurrentRow(0)
    first = tab.current.steps[0]
    tab.move_step(1)
    assert tab.current.steps[1] == first
    tab.delete_step()
    assert w.is_dirty


def test_tab_cap_on_manual_insert(env):
    w, *_ = env
    tab = w.macro_tab
    tab.macro_list.setCurrentRow(0)
    tab.current.steps = [DelayStep(1)] * 499
    tab.insert_key(E.KEY_A)  # +2 > 500
    assert len(tab.current.steps) == 499 and tab.hint.text()


def test_field_records_mouse_clicks_via_event_filter(env):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    w, *_ = env
    tab = w.macro_tab
    tab.macro_list.setCurrentRow(0)
    tab.delays_check.setChecked(False)
    tab.start_recording()
    QTest.mouseClick(tab.record_field, Qt.MouseButton.RightButton)
    tab.stop_recording()
    assert tab.current.steps[-2:] == [ButtonStep(E.BTN_RIGHT, True), ButtonStep(E.BTN_RIGHT, False)]
    QTest.mouseClick(tab.record_field, Qt.MouseButton.LeftButton)  # not recording: ignored
    assert len(tab.current.steps) == 4
