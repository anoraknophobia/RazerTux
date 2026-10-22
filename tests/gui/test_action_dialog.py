from evdev import ecodes as E

from naga_config.config.model import (
    ComboAction, DefaultAction, DisabledAction, DpiStageAction, HypershiftAction,
    KeyAction, Macro, MacroAction,
)
from naga_config.gui.action_dialog import ActionDialog
from naga_config.hw.buttons import ButtonId

CTRL, SHIFT, M = E.KEY_LEFTCTRL, E.KEY_LEFTSHIFT, E.KEY_M


def dlg(qapp, current=None, macros=None, button=ButtonId.SIDE_1, layer="standard"):
    return ActionDialog(None, current or DefaultAction(), macros or {},
                        button=button, layer=layer)


def type_names(d):
    return [d.type_combo.itemText(i) for i in range(d.type_combo.count())]


def test_returns_combo_from_simulated_input(qapp):
    d = dlg(qapp)
    d.select_type("combo")
    for c in (CTRL, SHIFT, M):
        d.capture.press(c)
    for c in (M, SHIFT, CTRL):
        d.capture.release(c)
    assert d.result_action() == ComboAction([CTRL, SHIFT, M])


def test_returns_single_key(qapp):
    d = dlg(qapp)
    d.select_type("key")
    d.capture.press(E.KEY_A)
    d.capture.release(E.KEY_A)
    assert d.result_action() == KeyAction(E.KEY_A)


def test_modifier_alone_is_a_key(qapp):
    d = dlg(qapp)
    d.select_type("key")
    d.capture.press(SHIFT)
    d.capture.release(SHIFT)
    assert d.result_action() == KeyAction(SHIFT)


def test_rerecord_replaces_previous(qapp):
    d = dlg(qapp)
    d.select_type("combo")
    d.capture.press(E.KEY_A)
    d.capture.release(E.KEY_A)
    d.capture.press(CTRL)
    d.capture.press(M)
    assert d.result_action() == ComboAction([CTRL, M])


def test_simple_types(qapp):
    d = dlg(qapp)
    for t, expected in [("default", DefaultAction()), ("disabled", DisabledAction()),
                        ("hypershift", HypershiftAction()),
                        ("dpi_up", DpiStageAction("up")), ("dpi_down", DpiStageAction("down")),
                        ("dpi_cycle", DpiStageAction("cycle"))]:
        d.select_type(t)
        assert d.result_action() == expected


def test_macro_mode_and_count(qapp):
    macros = {"m1": Macro("m1", "Burst")}
    d = dlg(qapp, macros=macros)
    d.select_type("macro")
    d.select_macro("m1")
    d.select_mode("repeat_n")
    d.count_spin.setValue(4)
    assert d.result_action() == MacroAction("m1", "repeat_n", 4)


def test_prefills_current(qapp):
    d = dlg(qapp, KeyAction(E.KEY_B))
    assert d.result_action() == KeyAction(E.KEY_B)
    d = dlg(qapp, MacroAction("m1", "toggle"), macros={"m1": Macro("m1", "X")})
    assert d.result_action() == MacroAction("m1", "toggle", 1)


def test_incomplete_input_is_not_ok(qapp):
    d = dlg(qapp)
    d.select_type("key")
    assert d.result_action() is None
    assert not d.ok_button.isEnabled()


def test_hypershift_option_rules(qapp):
    assert "Hypershift-Taste" in type_names(dlg(qapp))
    assert "Hypershift-Taste" not in type_names(dlg(qapp, layer="hypershift"))
    assert "Hypershift-Taste" not in type_names(dlg(qapp, button=ButtonId.WHEEL_UP))
    assert "Hypershift-Taste" not in type_names(dlg(qapp, button=ButtonId.WHEEL_LEFT))


def test_dpi_options_offered(qapp):
    names = type_names(dlg(qapp))
    assert "DPI-Stufe hoch" in names and "DPI-Stufe runter" in names


def test_macro_without_macros_disabled(qapp):
    d = dlg(qapp)
    d.select_type("macro")
    assert d.result_action() is None


def test_rerecord_does_not_leak_old_modifiers(qapp):
    d = dlg(qapp)
    d.select_type("combo")
    d.capture.press(CTRL)
    d.capture.press(E.KEY_Z)
    assert d.result_action() == ComboAction([CTRL, E.KEY_Z])
    d.capture.press(M)  # new recording, no release in between
    assert d.result_action() == KeyAction(M)


def test_rerecord_modifier_alone_after_combo(qapp):
    d = dlg(qapp)
    d.select_type("combo")
    d.capture.press(CTRL)
    d.capture.press(E.KEY_Z)
    d.capture.release(E.KEY_Z)
    d.capture.release(CTRL)
    d.capture.press(SHIFT)
    d.capture.release(SHIFT)
    assert d.result_action() == KeyAction(SHIFT)


def test_cycle_action_loads(qapp):
    d = dlg(qapp, current=DpiStageAction("cycle"))
    assert d.type_combo.currentData() == "dpi_cycle"
    assert d.result_action() == DpiStageAction("cycle")
