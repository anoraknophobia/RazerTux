import asyncio
import logging

from evdev import ecodes as E
from fake_output import FakeSleep, GateSleep, RecordingOutput

from naga_config.config.model import ButtonStep, DelayStep, KeyStep, Macro
from naga_config.engine.macro import MacroPlayer
from naga_config.hw.buttons import ButtonId

A, B = E.KEY_A, E.KEY_B
K = ButtonId.SIDE_1


def tap_macro():
    return Macro("m", "m", [KeyStep(A, True), DelayStep(10), KeyStep(A, False)])


async def settle(n=20):
    for _ in range(n):
        await asyncio.sleep(0)


async def test_once_runs_steps_and_delays():
    out, sl = RecordingOutput(), FakeSleep()
    p = MacroPlayer(out, sleep=sl)
    p.start(tap_macro(), "once", 1, K)
    await settle()
    assert out.log == [(A, 1), (A, 0)]
    assert sl.calls == [0.01]
    assert out.events[-1] == ("sync",)


async def test_repeat_n_runs_exactly_n():
    out = RecordingOutput()
    p = MacroPlayer(out, sleep=FakeSleep())
    p.start(tap_macro(), "repeat_n", 3, K)
    await settle(100)
    assert out.log == [(A, 1), (A, 0)] * 3


async def test_while_held_stops_on_release():
    out, sl = RecordingOutput(), FakeSleep()
    p = MacroPlayer(out, sleep=sl)
    p.start(tap_macro(), "while_held", 1, K)
    await settle(10)
    n = len(out.log)
    assert n >= 4  # looped more than once
    p.button_up(K)
    await settle(10)
    after = len(out.log)
    await settle(10)
    assert len(out.log) == after
    assert out.log[-1][1] == 0  # no key left down
    await p.stop_all()


async def test_toggle_second_press_stops():
    out = RecordingOutput()
    p = MacroPlayer(out, sleep=FakeSleep())
    p.start(tap_macro(), "toggle", 1, K)
    await settle(10)
    assert out.log
    p.start(tap_macro(), "toggle", 1, K)  # second press
    await settle(5)
    n = len(out.log)
    await settle(10)
    assert len(out.log) == n
    held = {}
    for c, v in out.log:
        held[c] = v
    assert not any(held.values())


async def test_repeat_press_while_once_running_ignored():
    out, sl = RecordingOutput(), GateSleep()
    p = MacroPlayer(out, sleep=sl)
    p.start(tap_macro(), "once", 1, K)
    await settle()
    p.start(tap_macro(), "once", 1, K)
    await settle()
    sl.release()
    await settle()
    assert out.log == [(A, 1), (A, 0)]


async def test_stop_mid_run_releases_held_keys():
    out, sl = RecordingOutput(), GateSleep()
    macro = Macro("m", "m", [KeyStep(A, True), ButtonStep(E.BTN_LEFT, True),
                             DelayStep(1000), KeyStep(A, False)])
    p = MacroPlayer(out, sleep=sl)
    p.start(macro, "while_held", 1, K)
    await settle()
    assert out.log == [(A, 1), (E.BTN_LEFT, 1)]
    p.button_up(K)
    await settle()
    assert out.log[2:] == [(E.BTN_LEFT, 0), (A, 0)]  # reverse press order
    assert out.events[-1] == ("sync",)


async def test_stop_all_releases_and_cancels():
    out, sl = RecordingOutput(), GateSleep()
    p = MacroPlayer(out, sleep=sl)
    p.start(tap_macro(), "once", 1, K)
    p.start(tap_macro(), "once", 1, ButtonId.SIDE_2)
    await settle()
    await p.stop_all()
    assert out.log.count((A, 0)) == 2
    sl.release()
    await settle()
    assert out.log.count((A, 0)) == 2


async def test_exception_in_macro_logged_not_raised(caplog):
    class Boom(RecordingOutput):
        def key(self, code, value):
            raise RuntimeError("boom")

    p = MacroPlayer(Boom(), sleep=FakeSleep())
    with caplog.at_level(logging.ERROR):
        p.start(tap_macro(), "once", 1, K)
        await settle()
    assert any("boom" in r.getMessage() or (r.exc_info and "boom" in str(r.exc_info[1]))
               for r in caplog.records)
    # player still usable
    p.start(Macro("x", "x", []), "once", 1, K)
    await settle()


async def test_empty_loop_sleeps_at_least_1ms_per_iteration():
    out, sl = RecordingOutput(), FakeSleep()
    p = MacroPlayer(out, sleep=sl)
    p.start(Macro("e", "e", []), "toggle", 1, K)
    await settle(30)
    assert len(sl.calls) >= 3
    assert all(c >= 0.001 for c in sl.calls)
    await p.stop_all()


async def test_delay_free_while_held_loop_sleeps():
    out, sl = RecordingOutput(), FakeSleep()
    p = MacroPlayer(out, sleep=sl)
    p.start(Macro("e", "e", [KeyStep(A, True), KeyStep(A, False)]), "while_held", 1, K)
    await settle(30)
    assert sl.calls and all(c >= 0.001 for c in sl.calls)
    await p.stop_all()


async def test_while_held_quick_tap_plays_one_full_iteration():
    out = RecordingOutput()
    p = MacroPlayer(out, sleep=FakeSleep())
    p.start(tap_macro(), "while_held", 1, K)
    p.button_up(K)  # released before the task ever ran
    await settle(30)
    assert out.log == [(A, 1), (A, 0)]
