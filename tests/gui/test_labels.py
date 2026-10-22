from evdev import ecodes as E

from razertux.config.model import (
    ComboAction, DefaultAction, DisabledAction, DpiStageAction, HypershiftAction,
    KeyAction, Macro, MacroAction,
)
from razertux.gui.labels import action_label
from razertux.hw.buttons import ButtonId


def L(a, button=ButtonId.SIDE_1, macros=None):
    return action_label(a, button, macros or {}, layout="de")


def test_labels():
    assert L(KeyAction(E.KEY_A)) == "A"
    assert L(ComboAction([E.KEY_LEFTCTRL, E.KEY_LEFTSHIFT, E.KEY_M])) == "Strg+Shift+M"
    assert L(DefaultAction()) == "Standard"
    assert L(DisabledAction()) == "Aus"
    assert L(HypershiftAction()) == "Hypershift"
    assert L(DpiStageAction("up")) == "DPI hoch"
    assert L(DpiStageAction("down")) == "DPI runter"
    assert L(DpiStageAction("cycle")) == "DPI-Stufe weiter"


def test_macro_label_and_missing():
    macros = {"m1": Macro("m1", "Burst")}
    assert L(MacroAction("m1", "once"), macros=macros) == "Makro: Burst"
    assert L(MacroAction("zz", "once"), macros=macros) == "Makro: ? (fehlt)"


def test_top_default_shows_dpi():
    assert L(DefaultAction(), ButtonId.TOP_1) == "DPI hoch"
    assert L(DefaultAction(), ButtonId.TOP_2) == "DPI-Stufe weiter"  # = default_profile (R20)
