import json

import pytest
from evdev import ecodes as E

from razertux.hw.buttons import ButtonId
from razertux.recorder.recording import (CHOICES, GuidedSteps, Recording, SignalSet, TEMPLATE_CHOICES,
                                         classify_standard_key, load_template)
from razertux.recorder.report import sanitize

MOUSE = "if0:mouse"
KBD = "if2:keyboard"


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def key(src, code):
    return (src, E.EV_KEY, code)


def test_choices_and_templates_load():
    assert CHOICES == ("ergo", "ambi", "compact", "vertical", "mmo", "generic")
    assert set(TEMPLATE_CHOICES) == set(CHOICES)
    for c in CHOICES:
        pairs = load_template(c)
        assert pairs and all(isinstance(b, ButtonId) and isinstance(e, str) for b, e in pairs)
    assert [b for b, _ in load_template("mmo")][:2] == [ButtonId.SIDE_1, ButtonId.SIDE_2]
    assert load_template("ergo")[0] == (ButtonId.RIGHT, "right")
    with pytest.raises(ValueError):
        load_template("../etc/passwd")


def test_guided_steps_one_per_button_in_order():
    steps = GuidedSteps([(ButtonId.LEFT, "left"), (ButtonId.MIDDLE, "wheel"), (ButtonId.WHEEL_UP, "wheel")])
    assert [s.button for s in steps] == [ButtonId.LEFT, ButtonId.MIDDLE, ButtonId.WHEEL_UP]
    assert all(s.hint and isinstance(s.hint, str) for s in steps)
    assert steps[1].hint != steps[2].hint


def test_classify_standard_key():
    assert classify_standard_key(E.KEY_H) and classify_standard_key(E.KEY_ENTER)
    assert not classify_standard_key(E.KEY_F13) and not classify_standard_key(E.BTN_LEFT)


def test_signalset_basic():
    s = SignalSet("mouse")
    assert s.add(*key(MOUSE, E.BTN_SIDE))
    assert not s.add(*key(MOUSE, E.BTN_SIDE))  # once
    assert s.contains(*key(MOUSE, E.BTN_SIDE))
    assert s.names() == ["if0:mouse:BTN_SIDE"]
    assert s.filtered(E.EV_KEY) == [key(MOUSE, E.BTN_SIDE)]
    assert s.filtered(E.EV_REL) == []


def test_signalset_drops_cursor_motion():
    s = SignalSet("mouse")
    assert not s.add(MOUSE, E.EV_REL, E.REL_X) and not s.add(MOUSE, E.EV_REL, E.REL_Y)
    assert s.add(MOUSE, E.EV_REL, E.REL_WHEEL, 1)
    assert s.names() == ["if0:mouse:REL_WHEEL:+"]


def test_hunter2_on_keyboard_stores_nothing():
    for kind in ("keyboard", "keypad", None):
        s = SignalSet(kind)
        for c in (E.KEY_H, E.KEY_U, E.KEY_N, E.KEY_T, E.KEY_E, E.KEY_R, E.KEY_2, E.KEY_ENTER):
            assert not s.add(*key(KBD, c))
        assert s.names() == [] and s.standard_keys_seen == 1  # sources only, no letters, digits or order
        assert "hunter2" not in repr(vars(s)).lower()


def test_keyboard_extra_keys_kept():
    s = SignalSet("keyboard")
    assert s.add(*key(KBD, E.KEY_F13)) and s.add(*key(KBD, E.KEY_VOLUMEUP))
    assert s.names() == ["if2:keyboard:KEY_F13", "if2:keyboard:KEY_VOLUMEUP"]


def test_naga_side_keys_on_mouse_are_stored():
    s = SignalSet("mouse")
    for c in (E.KEY_1, E.KEY_2, E.KEY_MINUS, E.KEY_EQUAL):
        assert s.add(*key(KBD, c))
    assert not s.add(*key(KBD, E.KEY_A))  # a letter on a mouse's keyboard node is not a side button
    assert s.standard_keys_seen == 1
    assert s.names()[0] == "if2:keyboard:KEY_1"


def test_guided_recording_report_shape():
    clock = Clock()
    steps = GuidedSteps([(ButtonId.SIDE_1, "s1"), (ButtonId.SIDE_2, "s2"), (ButtonId.SIDE_3, "s3")])
    r = Recording("mouse", "mmo", steps, clock=clock)
    r.start()
    assert r.current.button == ButtonId.SIDE_1
    r.record(*key(KBD, E.KEY_1))
    r.record(*key(KBD, E.KEY_1))
    r.next()
    r.absent()
    assert r.current.button == ButtonId.SIDE_3
    assert not r.finished
    b = r.finish(standard_keys_seen=0)
    assert b == {"template": "mmo", "results": {"side_1": ["if2:keyboard:KEY_1"], "side_2": "absent"},
                 "signals": [], "standard_keys_seen": 0, "duplicates": []}
    assert sanitize({"buttons": b})["buttons"] == b


def test_free_recording_collects_signals():
    r = Recording("mouse", "generic", None, clock=Clock())
    r.start()
    assert r.current is None
    r.record(MOUSE, E.EV_KEY, E.BTN_EXTRA)
    r.record(MOUSE, E.EV_KEY, E.BTN_EXTRA)
    r.record(MOUSE, E.EV_REL, E.REL_HWHEEL, -1)
    b = r.finish()
    assert b["results"] == {} and b["signals"] == ["if0:mouse:BTN_EXTRA", "if0:mouse:REL_HWHEEL:-"]


def test_recording_auto_stop_after_120s():
    clock = Clock()
    r = Recording("mouse", "generic", GuidedSteps([(ButtonId.LEFT, "left")]), clock=clock)
    r.start()
    clock.t += 119
    assert not r.expired
    assert r.record(*key(MOUSE, E.BTN_LEFT))
    clock.t += 1
    assert r.expired and r.finished
    assert not r.record(*key(MOUSE, E.BTN_RIGHT))  # ignored after the stop
    b = r.finish()  # the step in progress is cut off: marked, not reported
    assert b["results"] == {} and b["timed_out"] and b["stopped_at"] == "left"


def test_recording_must_start_and_finish_ends():
    r = Recording("mouse", "ergo", GuidedSteps([(ButtonId.LEFT, "left")]), clock=Clock())
    with pytest.raises(RuntimeError):
        r.record(*key(MOUSE, E.BTN_LEFT))
    r.start()
    r.next()
    assert r.current is None and r.finished
    r.finish()
    assert r.finished


def test_standard_keys_seen_is_int():
    r = Recording("keyboard", "generic", None, clock=Clock())
    r.start()
    for c in (E.KEY_H, E.KEY_ENTER):
        r.record(*key(KBD, c))
    b = r.finish(standard_keys_seen=1)
    assert b["signals"] == [] and b["standard_keys_seen"] == 1
    assert "hunter2" not in json.dumps(b)


def test_wheel_direction_kept():
    s = SignalSet("mouse")
    assert s.add(MOUSE, E.EV_REL, E.REL_WHEEL, 1) and s.add(MOUSE, E.EV_REL, E.REL_WHEEL, -1)
    assert not s.add(MOUSE, E.EV_REL, E.REL_WHEEL, 1)
    assert not s.add(MOUSE, E.EV_REL, E.REL_HWHEEL)  # direction missing
    assert s.add(MOUSE, E.EV_REL, E.REL_HWHEEL, -1)
    assert s.names() == ["if0:mouse:REL_WHEEL:+", "if0:mouse:REL_WHEEL:-", "if0:mouse:REL_HWHEEL:-"]


def test_wheel_up_and_down_steps_differ():
    steps = GuidedSteps([(ButtonId.WHEEL_UP, "wheel"), (ButtonId.WHEEL_DOWN, "wheel")])
    assert steps[0].hint == "Scroll the wheel up" and steps[1].hint == "Scroll the wheel down"
    r = Recording("mouse", "ergo", steps, clock=Clock())
    r.start()
    r.record(MOUSE, E.EV_REL, E.REL_WHEEL, 1)
    r.next()
    r.record(MOUSE, E.EV_REL, E.REL_WHEEL, -1)
    b = r.finish()
    assert b["results"] == {"wheel_up": ["if0:mouse:REL_WHEEL:+"], "wheel_down": ["if0:mouse:REL_WHEEL:-"]}
    assert b["duplicates"] == []


def test_abs_signals_capped():
    s = SignalSet("mouse")
    for c in range(40):
        s.add(MOUSE, E.EV_ABS, c)
    assert len(s.filtered(E.EV_ABS)) == 32


def test_std_sources_counted_once_across_steps():
    r = Recording("keyboard", "generic", GuidedSteps([(ButtonId.LEFT, "l"), (ButtonId.RIGHT, "r")]), clock=Clock())
    r.start()
    r.record(*key(KBD, E.KEY_H))
    r.next()
    r.record(*key(KBD, E.KEY_I))
    assert r.finish()["standard_keys_seen"] == 1


def test_timeout_marks_step_and_duplicates_flagged():
    clock = Clock()
    steps = GuidedSteps([(ButtonId.LEFT, "l"), (ButtonId.RIGHT, "r"), (ButtonId.MIDDLE, "m")])
    r = Recording("mouse", "ergo", steps, clock=clock)
    r.start()
    r.record(*key(MOUSE, E.BTN_SIDE))
    r.next()
    r.record(*key(MOUSE, E.BTN_SIDE))
    r.next()
    r.record(*key(MOUSE, E.BTN_EXTRA))
    clock.t += 121
    b = r.finish()
    assert b["timed_out"] is True and b["stopped_at"] == "middle" and "middle" not in b["results"]
    assert b["duplicates"] == ["if0:mouse:BTN_SIDE"]
    assert sanitize({"buttons": b})["buttons"] == b


def test_start_resets_everything():
    r = Recording("mouse", "ergo", GuidedSteps([(ButtonId.LEFT, "l")]), clock=Clock())
    r.start()
    r.record(*key(MOUSE, E.BTN_LEFT))
    r.next()
    r.start()
    assert r.current.button == ButtonId.LEFT and r.finish()["results"] == {}


def test_no_signal_drops_what_the_step_caught():
    """A wizard button clicked with the recorded mouse must not leave its BTN_LEFT behind."""
    steps = GuidedSteps([(ButtonId.TOP_1, "t1"), (ButtonId.TOP_2, "t2")])
    r = Recording("mouse", "generic", steps, clock=Clock())
    r.start()
    r.record(*key(MOUSE, E.BTN_LEFT))
    assert r.caught == ["if0:mouse:BTN_LEFT"]
    r.no_signal()
    assert r.caught == []
    assert r.current.button == ButtonId.TOP_2
    assert r.finish()["results"] == {"top_1": []}



def test_report_lists_are_sorted_never_in_press_order():
    """Ruling R-6: no order is stored, so digits typed on a mouse's keyboard node (a PIN on the
    number grid) cannot be read back from the report."""
    for order in ([E.KEY_3, E.KEY_1, E.KEY_2], [E.KEY_2, E.KEY_3, E.KEY_1]):
        r = Recording("mouse", "free", clock=lambda: 0.0)
        r.start()
        for code in order:
            r.record(KBD, E.EV_KEY, code)
        assert r.finish()["signals"] == ["if2:keyboard:KEY_1", "if2:keyboard:KEY_2", "if2:keyboard:KEY_3"]


def test_guided_results_and_duplicates_are_sorted():
    steps = GuidedSteps([(ButtonId.SIDE_1, "s1"), (ButtonId.SIDE_2, "s2")])
    r = Recording("mouse", "mmo", steps, clock=lambda: 0.0)
    r.start()
    for code in (E.KEY_9, E.KEY_4):
        r.record(KBD, E.EV_KEY, code)
    r.next()
    for code in (E.KEY_9, E.KEY_4):
        r.record(KBD, E.EV_KEY, code)
    out = r.finish()
    assert out["results"]["side_1"] == ["if2:keyboard:KEY_4", "if2:keyboard:KEY_9"]
    assert out["results"]["side_2"] == ["if2:keyboard:KEY_4", "if2:keyboard:KEY_9"]
    assert out["duplicates"] == ["if2:keyboard:KEY_4", "if2:keyboard:KEY_9"]
