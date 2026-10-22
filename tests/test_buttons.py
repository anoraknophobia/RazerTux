import re
from pathlib import Path

import pytest
from evdev import ecodes as E

from razertux.hw.buttons import (
    DRIVER_MODE,
    NAGA_DEVICE_SUFFIXES,
    NORMAL_MODE,
    TOP_BUTTON_CODES,
    ButtonId,
    identify,
    identify_vendor,
    parse_notification,
)

PROBE = Path(__file__).parent.parent / "tools" / "probe-2026-10-22.txt"
DRIVER_PROBE = Path(__file__).parent.parent / "tools" / "probe-2026-10-22-driver-mode.txt"

SIDE_KEYS = [E.KEY_1, E.KEY_2, E.KEY_3, E.KEY_4, E.KEY_5, E.KEY_6, E.KEY_7,
             E.KEY_8, E.KEY_9, E.KEY_0, E.KEY_MINUS, E.KEY_EQUAL]


def test_side_keys_from_if02():
    assert identify("if02", E.EV_KEY, E.KEY_1, 1) == ButtonId.SIDE_1
    assert identify("if02", E.EV_KEY, E.KEY_0, 1) == ButtonId.SIDE_10
    assert identify("if02", E.EV_KEY, E.KEY_MINUS, 0) == ButtonId.SIDE_11
    assert identify("if02", E.EV_KEY, E.KEY_EQUAL, 1) == ButtonId.SIDE_12


@pytest.mark.parametrize("n,key", list(enumerate(SIDE_KEYS, start=1)))
def test_all_side_keys(n, key):
    assert identify("if02", E.EV_KEY, key, 1) == ButtonId(f"side_{n}")


@pytest.mark.parametrize("source", ["mouse", "if01", "if01_event"])
def test_side_keys_only_from_if02(source):
    assert identify(source, E.EV_KEY, E.KEY_1, 1) is None


def test_mouse_buttons():
    assert identify("mouse", E.EV_KEY, E.BTN_LEFT, 1) == ButtonId.LEFT
    assert identify("mouse", E.EV_KEY, E.BTN_RIGHT, 0) == ButtonId.RIGHT
    assert identify("mouse", E.EV_KEY, E.BTN_MIDDLE, 1) == ButtonId.MIDDLE


def test_wheel_and_tilt():
    assert identify("mouse", E.EV_REL, E.REL_WHEEL, 1) == ButtonId.WHEEL_UP
    assert identify("mouse", E.EV_REL, E.REL_WHEEL, -1) == ButtonId.WHEEL_DOWN
    assert identify("mouse", E.EV_REL, E.REL_HWHEEL, -1) == ButtonId.WHEEL_LEFT
    assert identify("mouse", E.EV_REL, E.REL_HWHEEL, 1) == ButtonId.WHEEL_RIGHT
    assert identify("mouse", E.EV_REL, E.REL_WHEEL, 3) == ButtonId.WHEEL_UP
    assert identify("mouse", E.EV_REL, E.REL_WHEEL, -2) == ButtonId.WHEEL_DOWN


@pytest.mark.parametrize("code", [E.REL_WHEEL_HI_RES, E.REL_HWHEEL_HI_RES, E.REL_X, E.REL_Y])
def test_other_rel_ignored(code):
    assert identify("mouse", E.EV_REL, code, 120) is None


def test_msc_syn_ignored():
    assert identify("if02", E.EV_MSC, E.MSC_SCAN, 458782) is None
    assert identify("mouse", E.EV_SYN, 0, 0) is None


def test_unknown_source_ignored():
    assert identify("bogus", E.EV_KEY, E.BTN_LEFT, 1) is None


def test_device_suffixes():
    assert NAGA_DEVICE_SUFFIXES == {
        "event-mouse": "mouse",
        "if01-event-kbd": "if01",
        "if02-event-kbd": "if02",
        "event-if01": "if01_event",
    }


def test_button_values_are_lowercase_names():
    assert ButtonId.SIDE_1 == "side_1"
    assert ButtonId.WHEEL_LEFT.value == "wheel_left"
    assert len(ButtonId) == 12 + 3 + 4 + 2


def test_probe_recording_maps_to_side_buttons_in_order():
    seen = []
    for line in PROBE.read_text().splitlines():
        m = re.match(r"\s*[\d.]+ ev:if02-event-kbd\s+EV_KEY (KEY_\w+) 1$", line)
        if m:
            seen.append(identify("if02", E.EV_KEY, getattr(E, m.group(1)), 1))
    firsts = list(dict.fromkeys(seen))
    assert firsts[-12:] == [ButtonId(f"side_{n}") for n in range(1, 13)]
    assert None not in seen


def test_vendor_constants():
    assert (NORMAL_MODE, DRIVER_MODE) == (0x00, 0x03)
    assert TOP_BUTTON_CODES == {0x57: ButtonId.TOP_1, 0x52: ButtonId.TOP_2}


def test_vendor_reports_from_driver_mode_recording():
    reports = {}
    for line in DRIVER_PROBE.read_text().splitlines():
        if "raw:/dev/hidraw4" in line:
            data = bytes.fromhex(line.split("RAW")[1].strip())
            reports.setdefault(data[:2], data)
    assert identify_vendor(reports[b"\x04\x57"]) == (ButtonId.TOP_1, True)
    assert identify_vendor(reports[b"\x04\x52"]) == (ButtonId.TOP_2, True)
    assert identify_vendor(reports[b"\x04\x00"]) == (None, False)
    assert identify_vendor(reports[b"\x05\x31"]) is None
    assert identify_vendor(reports[b"\x05\x0e"]) is None


def test_vendor_unknown_or_empty():
    assert identify_vendor(bytes([0x04, 0x99] + [0] * 14)) is None
    assert identify_vendor(b"\x04") is None
    assert identify_vendor(b"") is None


# -- vendor notifications (scroll stage / DPI, both device modes) ---------------

@pytest.mark.parametrize("slot,preset", [(2, 0x81), (3, 0x82), (4, 0x84), (5, 0x85), (6, 0x86)])
def test_parse_scroll_stage(slot, preset):
    assert parse_notification(bytes([0x05, 0x3A, slot, preset]) + bytes(12)) == ("scroll_stage", slot)


def test_parse_dpi():
    assert parse_notification(bytes.fromhex("050209c409c4") + bytes(10)) == ("dpi", 2500)
    assert parse_notification(bytes.fromhex("05020320 0640".replace(" ", ""))) == ("dpi", 800)


@pytest.mark.parametrize("report", [
    bytes([0x05, 0x31, 0xC1, 1, 1, 1]),  # periodic
    bytes([0x05, 0x0E, 0, 0]),
    bytes([0x04, 0x57]) + bytes(14),     # top button (driver mode)
    bytes([0x04, 0x00]) + bytes(14),
    bytes([0x05, 0x3A]),                 # truncated
    bytes([0x05, 0x02, 0x09, 0xC4, 0x09]),
    b"",
])
def test_parse_notification_ignores_other_reports(report):
    assert parse_notification(report) is None
