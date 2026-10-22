"""Button identification for the Razer Naga V2 Pro (from real hardware probe).

See tools/probe-2026-10-22*.txt. Side keys arrive as keyboard events on the
if02 keyboard node; wheel/tilt on the mouse node; the two top buttons only
exist as vendor hidraw reports while the device is in driver mode.
"""
from __future__ import annotations

from enum import StrEnum

from evdev import ecodes as E


class ButtonId(StrEnum):
    SIDE_1 = "side_1"
    SIDE_2 = "side_2"
    SIDE_3 = "side_3"
    SIDE_4 = "side_4"
    SIDE_5 = "side_5"
    SIDE_6 = "side_6"
    SIDE_7 = "side_7"
    SIDE_8 = "side_8"
    SIDE_9 = "side_9"
    SIDE_10 = "side_10"
    SIDE_11 = "side_11"
    SIDE_12 = "side_12"
    LEFT = "left"
    RIGHT = "right"
    MIDDLE = "middle"
    WHEEL_UP = "wheel_up"
    WHEEL_DOWN = "wheel_down"
    WHEEL_LEFT = "wheel_left"
    WHEEL_RIGHT = "wheel_right"
    TOP_1 = "top_1"
    TOP_2 = "top_2"


# by-id suffix -> source name used by identify()
NAGA_DEVICE_SUFFIXES = {
    "event-mouse": "mouse",
    "if01-event-kbd": "if01",
    "if02-event-kbd": "if02",
    "event-if01": "if01_event",
}

# Device modes (feature report); top buttons only report in driver mode.
NORMAL_MODE = 0x00
DRIVER_MODE = 0x03

# hidraw (if01) input report 0x04, byte 1 -> top button
TOP_BUTTON_CODES = {0x57: ButtonId.TOP_1, 0x52: ButtonId.TOP_2}

_SIDE_KEYS = {
    key: ButtonId(f"side_{n}")
    for n, key in enumerate(
        [E.KEY_1, E.KEY_2, E.KEY_3, E.KEY_4, E.KEY_5, E.KEY_6, E.KEY_7,
         E.KEY_8, E.KEY_9, E.KEY_0, E.KEY_MINUS, E.KEY_EQUAL],
        start=1,
    )
}
_MOUSE_KEYS = {
    E.BTN_LEFT: ButtonId.LEFT,
    E.BTN_RIGHT: ButtonId.RIGHT,
    E.BTN_MIDDLE: ButtonId.MIDDLE,
}


def identify(source: str, ev_type: int, code: int, value: int) -> ButtonId | None:
    """Map an evdev event to a button, or None if it is not a button event."""
    if ev_type == E.EV_KEY:
        if source == "if02":
            return _SIDE_KEYS.get(code)
        if source == "mouse":
            return _MOUSE_KEYS.get(code)
    elif ev_type == E.EV_REL and source == "mouse":
        if code == E.REL_WHEEL and value:
            return ButtonId.WHEEL_UP if value > 0 else ButtonId.WHEEL_DOWN
        if code == E.REL_HWHEEL and value:
            return ButtonId.WHEEL_RIGHT if value > 0 else ButtonId.WHEEL_LEFT
    return None


def identify_vendor(report: bytes) -> tuple[ButtonId | None, bool] | None:
    """Decode a hidraw input report (driver mode).

    Returns (button, True) for a top-button press, (None, False) for the
    release of whichever top button is down, and None for anything else.
    """
    if len(report) < 2 or report[0] != 0x04:
        return None
    if report[1] == 0x00:
        return (None, False)
    button = TOP_BUTTON_CODES.get(report[1])
    return (button, True) if button else None
