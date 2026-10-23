import pytest
from evdev import ecodes as E

from razertux.devices.db import load_devices
from razertux.hw import buttons
from razertux.hw.button_maps import (GENERIC, NAGA_FAMILY, NAGA_V2_PRO, InputSignal,
                                     identify, map_for)
from razertux.hw.buttons import ButtonId


def test_new_button_ids():
    assert ButtonId.BACK == "back" and ButtonId.LEARN_16 == "learn_16"
    assert ButtonId.DPI_UP == "dpi_up" and ButtonId.PROFILE == "profile"


@pytest.mark.parametrize("source,code", [
    ("if02", c) for c in (E.KEY_1, E.KEY_0, E.KEY_MINUS, E.KEY_EQUAL, E.KEY_F13)
] + [("mouse", c) for c in (E.BTN_LEFT, E.BTN_RIGHT, E.BTN_MIDDLE, E.BTN_SIDE)] + [("if01", E.KEY_1)])
def test_naga_v2_pro_matches_legacy_identify(source, code):
    got = identify(NAGA_V2_PRO, source, E.EV_KEY, code, 1)
    assert got == buttons.identify(source, E.EV_KEY, code, 1)


def test_wheel_independent_of_table():
    assert identify({}, "mouse", E.EV_REL, E.REL_WHEEL, 1) == ButtonId.WHEEL_UP
    assert identify({}, "mouse", E.EV_REL, E.REL_HWHEEL, -1) == ButtonId.WHEEL_LEFT
    assert identify({}, "mouse", E.EV_REL, E.REL_WHEEL, 0) is None


def test_generic_side_buttons():
    assert identify(GENERIC, "mouse", E.EV_KEY, E.BTN_SIDE, 1) == ButtonId.BACK
    assert identify(GENERIC, "mouse", E.EV_KEY, E.BTN_EXTRA, 1) == ButtonId.FORWARD


def test_naga_family_side_keys():
    assert identify(NAGA_FAMILY, "if02", E.EV_KEY, E.KEY_EQUAL, 1) == ButtonId.SIDE_12


def test_map_for_selection_and_learned():
    devs = load_devices()
    naga = next(d for d in devs.values() if d.model == "naga_v2_pro")
    other = next(d for d in devs.values() if d.model.startswith("naga") and d.model != "naga_v2_pro")
    plain = next(d for d in devs.values() if not d.model.startswith("naga"))
    assert map_for(naga, {}) == NAGA_V2_PRO
    assert map_for(other, {}) == {**NAGA_FAMILY, **GENERIC}
    assert map_for(plain, {}) == GENERIC
    sig = InputSignal("if01", E.EV_KEY, E.KEY_F13)
    m = map_for(plain, {ButtonId.LEARN_1: sig})
    assert identify(m, "if01", E.EV_KEY, E.KEY_F13, 1) == ButtonId.LEARN_1
    over = InputSignal("mouse", E.EV_KEY, E.BTN_SIDE)
    assert identify(map_for(plain, {ButtonId.DPI_UP: over}), "mouse", E.EV_KEY, E.BTN_SIDE, 1) == ButtonId.DPI_UP


def test_plate_tables_are_a_subset_of_the_12_key_plate_and_marked_unverified():
    naga = load_devices()["naga_v2_pro"]
    full = map_for(naga, {})
    assert map_for(naga, {}, plate=12) == full
    for plate in (6, 2):
        table = map_for(naga, {}, plate=plate)
        sides = {b for b in table.values() if b.value.startswith("side_")}
        assert sides == {ButtonId(f"side_{n}") for n in range(1, plate + 1)}
        assert identify(table, "if02", E.EV_KEY, E.KEY_1, 1) == ButtonId.SIDE_1
        assert identify(table, "mouse", E.EV_KEY, E.BTN_LEFT, 1) == ButtonId.LEFT
    assert identify(map_for(naga, {}, plate=6), "if02", E.EV_KEY, E.KEY_7, 1) is None
    # learned signals still win, other models ignore the plate
    sig = InputSignal("if02", E.EV_KEY, E.KEY_F13)
    assert identify(map_for(naga, {ButtonId.SIDE_8: sig}, plate=6), "if02", E.EV_KEY, E.KEY_F13, 1) == ButtonId.SIDE_8
    plain = next(d for d in load_devices().values() if not d.model.startswith("naga"))
    assert map_for(plain, {}, plate=6) == GENERIC
