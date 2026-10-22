import asyncio

import pytest
from evdev import ecodes as E
from fake_output import FakeSleep, GateSleep, RecordingOutput

from naga_config.config.model import (
    ComboAction,
    DefaultAction,
    DisabledAction,
    DpiStageAction,
    DelayStep,
    HypershiftAction,
    KeyAction,
    KeyStep,
    Macro,
    MacroAction,
    Profile,
)
from naga_config.engine.mapper import Mapper
from naga_config.hw.buttons import ButtonId as B


def mk(std=None, hs=None, macros=None, sleep=None):
    out = RecordingOutput()
    dpi = []
    prof = Profile("p", "p", standard=std or {}, hypershift=hs or {})
    m = Mapper(prof, macros or {}, out, on_dpi_stage=dpi.append,
               sleep=sleep or FakeSleep())
    return m, out, dpi


async def settle(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


async def test_combo_press_release_order():
    m, out, _ = mk({B.SIDE_1: ComboAction([E.KEY_LEFTCTRL, E.KEY_LEFTSHIFT, E.KEY_M])})
    await m.handle(B.SIDE_1, 1)
    await m.handle(B.SIDE_1, 0)
    assert out.log == [(E.KEY_LEFTCTRL, 1), (E.KEY_LEFTSHIFT, 1), (E.KEY_M, 1),
                       (E.KEY_M, 0), (E.KEY_LEFTSHIFT, 0), (E.KEY_LEFTCTRL, 0)]
    assert out.events[-1] == ("sync",)


async def test_combo_autorepeat_last_code_only():
    m, out, _ = mk({B.SIDE_1: ComboAction([E.KEY_LEFTCTRL, E.KEY_M])})
    await m.handle(B.SIDE_1, 1)
    await m.handle(B.SIDE_1, 2)
    assert out.log[-1] == (E.KEY_M, 2)
    assert len(out.log) == 3


async def test_key_action_and_repeat():
    m, out, _ = mk({B.SIDE_1: KeyAction(E.KEY_F1)})
    for v in (1, 2, 0):
        await m.handle(B.SIDE_1, v)
    assert out.log == [(E.KEY_F1, 1), (E.KEY_F1, 2), (E.KEY_F1, 0)]


async def test_hypershift_layer_and_release_after_shift_up():
    m, out, _ = mk(
        {B.TOP_1: HypershiftAction(), B.SIDE_1: KeyAction(E.KEY_F1)},
        {B.SIDE_1: KeyAction(E.KEY_F2)},
    )
    await m.handle(B.TOP_1, 1)
    await m.handle(B.SIDE_1, 1)
    await m.handle(B.TOP_1, 0)
    await m.handle(B.SIDE_1, 0)
    assert out.log == [(E.KEY_F2, 1), (E.KEY_F2, 0)]
    await m.handle(B.SIDE_1, 1)  # layer back to standard
    assert out.log[-1] == (E.KEY_F1, 1)


async def test_hypershift_missing_key_is_default():
    m, out, _ = mk({B.TOP_2: HypershiftAction(), B.SIDE_1: KeyAction(E.KEY_F1)}, {})
    await m.handle(B.TOP_2, 1)
    await m.handle(B.SIDE_1, 1)
    assert out.log == [(E.KEY_1, 1)]


async def test_profile_switch_releases_held_keys():
    m, out, _ = mk({B.SIDE_1: ComboAction([E.KEY_LEFTCTRL, E.KEY_M]),
                    B.SIDE_2: KeyAction(E.KEY_F1)})
    await m.handle(B.SIDE_1, 1)
    await m.handle(B.SIDE_2, 1)
    out.log.clear()
    await m.set_profile(Profile("q", "q", standard={B.SIDE_1: KeyAction(E.KEY_X)}))
    assert out.log == [(E.KEY_F1, 0), (E.KEY_M, 0), (E.KEY_LEFTCTRL, 0)]
    out.log.clear()
    await m.handle(B.SIDE_1, 0)  # stale release is ignored
    assert out.log == []
    await m.handle(B.SIDE_1, 1)
    assert out.log == [(E.KEY_X, 1)]


async def test_release_all_resets_hypershift():
    m, out, _ = mk({B.TOP_1: HypershiftAction(), B.SIDE_1: KeyAction(E.KEY_F1)},
                   {B.SIDE_1: KeyAction(E.KEY_F2)})
    await m.handle(B.TOP_1, 1)
    await m.release_all()
    await m.handle(B.SIDE_1, 1)
    assert out.log == [(E.KEY_F1, 1)]


async def test_default_passes_through():
    m, out, _ = mk()
    await m.handle(B.SIDE_1, 1)
    await m.handle(B.SIDE_1, 2)
    await m.handle(B.SIDE_1, 0)
    assert out.log == [(E.KEY_1, 1), (E.KEY_1, 2), (E.KEY_1, 0)]
    assert out.events[-1] == ("sync",)


@pytest.mark.parametrize("n,code", [
    (1, E.KEY_1), (9, E.KEY_9), (10, E.KEY_0), (11, E.KEY_MINUS), (12, E.KEY_EQUAL)])
async def test_default_side_table(n, code):
    m, out, _ = mk()
    await m.handle(B(f"side_{n}"), 1)
    assert out.log == [(code, 1)]


@pytest.mark.parametrize("b,code", [
    (B.LEFT, E.BTN_LEFT), (B.RIGHT, E.BTN_RIGHT), (B.MIDDLE, E.BTN_MIDDLE)])
async def test_default_mouse_buttons(b, code):
    m, out, _ = mk()
    await m.handle(b, 1)
    await m.handle(b, 0)
    assert out.log == [(code, 1), (code, 0)]


@pytest.mark.parametrize("b,axis,v,hi", [
    (B.WHEEL_UP, E.REL_WHEEL, 1, 120), (B.WHEEL_DOWN, E.REL_WHEEL, -1, -120),
    (B.WHEEL_LEFT, E.REL_HWHEEL, -1, -120), (B.WHEEL_RIGHT, E.REL_HWHEEL, 1, 120)])
async def test_default_wheel(b, axis, v, hi):
    m, out, _ = mk()
    await m.handle(b, 1)
    hi_axis = E.REL_WHEEL_HI_RES if axis == E.REL_WHEEL else E.REL_HWHEEL_HI_RES
    assert out.rels == [(axis, v), (hi_axis, hi)]
    assert out.events[-1] == ("sync",)


async def test_default_top_buttons_call_dpi_callback_on_press_only():
    m, out, dpi = mk()
    await m.handle(B.TOP_1, 1)
    await m.handle(B.TOP_1, 0)
    await m.handle(B.TOP_2, 1)
    await m.handle(B.TOP_2, 0)
    assert dpi == ["up", "down"]
    assert out.events == []


async def test_dpi_stage_action():
    m, out, dpi = mk({B.SIDE_3: DpiStageAction("down")})
    await m.handle(B.SIDE_3, 1)
    await m.handle(B.SIDE_3, 0)
    assert dpi == ["down"]
    assert out.log == []


async def test_disabled_does_nothing():
    m, out, _ = mk({B.SIDE_1: DisabledAction(), B.WHEEL_UP: DisabledAction()})
    await m.handle(B.SIDE_1, 1)
    await m.handle(B.SIDE_1, 0)
    await m.handle(B.WHEEL_UP, 1)
    assert out.events == []


async def test_wheel_bound_to_key_is_tap():
    m, out, _ = mk({B.WHEEL_UP: KeyAction(E.KEY_F5),
                    B.WHEEL_DOWN: ComboAction([E.KEY_LEFTCTRL, E.KEY_F6])})
    await m.handle(B.WHEEL_UP, 1)
    assert out.log == [(E.KEY_F5, 1), (E.KEY_F5, 0)]
    assert out.events == [("key", E.KEY_F5, 1), ("sync",), ("key", E.KEY_F5, 0), ("sync",)]
    out.log.clear()
    await m.handle(B.WHEEL_DOWN, 1)
    assert out.log == [(E.KEY_LEFTCTRL, 1), (E.KEY_F6, 1), (E.KEY_F6, 0), (E.KEY_LEFTCTRL, 0)]


async def test_wheel_cannot_be_hypershift():
    m, out, _ = mk({B.WHEEL_UP: HypershiftAction(), B.SIDE_1: KeyAction(E.KEY_F1)},
                   {B.SIDE_1: KeyAction(E.KEY_F2)})
    await m.handle(B.WHEEL_UP, 1)
    await m.handle(B.SIDE_1, 1)
    assert out.log == [(E.KEY_F1, 1)]


MACRO = Macro("m", "m", [KeyStep(E.KEY_A, True), KeyStep(E.KEY_A, False)])


async def test_macro_once_via_mapper():
    m, out, _ = mk({B.SIDE_1: MacroAction("m", "once")}, macros={"m": MACRO})
    await m.handle(B.SIDE_1, 1)
    await settle()
    await m.handle(B.SIDE_1, 0)
    assert out.log == [(E.KEY_A, 1), (E.KEY_A, 0)]


async def test_macro_while_held_via_mapper_stops_on_release():
    m, out, _ = mk({B.SIDE_1: MacroAction("m", "while_held")}, macros={"m": MACRO})
    await m.handle(B.SIDE_1, 1)
    await settle(10)
    assert len(out.log) > 2
    await m.handle(B.SIDE_1, 0)
    await settle(5)
    n = len(out.log)
    await settle(10)
    assert len(out.log) == n
    await m.release_all()


async def test_wheel_macro_runs_once_even_for_while_held():
    m, out, _ = mk({B.WHEEL_UP: MacroAction("m", "while_held")}, macros={"m": MACRO})
    await m.handle(B.WHEEL_UP, 1)
    await settle(20)
    assert out.log == [(E.KEY_A, 1), (E.KEY_A, 0)]


async def test_wheel_macro_toggle_toggles():
    m, out, _ = mk({B.WHEEL_UP: MacroAction("m", "toggle")}, macros={"m": MACRO})
    await m.handle(B.WHEEL_UP, 1)
    await settle(10)
    assert len(out.log) > 2  # toggle loops until pressed again
    await m.handle(B.WHEEL_UP, 1)
    await settle(5)
    n = len(out.log)
    await settle(10)
    assert len(out.log) == n


async def test_missing_macro_logs_and_does_nothing(caplog):
    m, out, _ = mk({B.SIDE_1: MacroAction("nope", "once")})
    await m.handle(B.SIDE_1, 1)
    await m.handle(B.SIDE_1, 0)
    assert out.events == []
    assert "nope" in caplog.text


async def test_profile_switch_during_running_macro_releases_its_keys():
    sl = GateSleep()
    macro = Macro("m", "m", [KeyStep(E.KEY_A, True), DelayStep(500), KeyStep(E.KEY_A, False)])
    m, out, _ = mk({B.SIDE_1: MacroAction("m", "once")}, macros={"m": macro}, sleep=sl)
    await m.handle(B.SIDE_1, 1)
    await settle()
    assert out.log == [(E.KEY_A, 1)]
    await m.set_profile(Profile("q", "q"))
    assert out.log == [(E.KEY_A, 1), (E.KEY_A, 0)]
    sl.release()
    await settle()
    assert out.log == [(E.KEY_A, 1), (E.KEY_A, 0)]


async def test_set_macros():
    m, out, _ = mk({B.SIDE_1: MacroAction("m", "once")})
    m.set_macros({"m": MACRO})
    await m.handle(B.SIDE_1, 1)
    await settle()
    assert out.log == [(E.KEY_A, 1), (E.KEY_A, 0)]


async def test_press_during_set_profile_uses_new_profile_and_toggle_macro_stopped():
    sl = GateSleep()
    macro = Macro("m", "m", [KeyStep(E.KEY_A, True), DelayStep(500), KeyStep(E.KEY_A, False)])
    m, out, _ = mk({B.SIDE_1: MacroAction("m", "toggle")}, macros={"m": macro}, sleep=sl)
    await m.handle(B.SIDE_1, 1)
    await settle()
    new = Profile("q", "q", standard={B.SIDE_2: KeyAction(E.KEY_X),
                                      B.SIDE_1: MacroAction("m", "toggle")})
    task = asyncio.get_running_loop().create_task(m.set_profile(new))
    await asyncio.sleep(0)  # set_profile is now suspended in its join
    await m.handle(B.SIDE_2, 1)
    assert (E.KEY_X, 1) in out.log
    await m.handle(B.SIDE_1, 1)  # new toggle macro starts fresh, old one is gone
    await task
    sl.release()
    await settle()
    await m.release_all()
    held = {}
    for c, v in out.log:
        held[c] = v
    assert not any(held.values())
