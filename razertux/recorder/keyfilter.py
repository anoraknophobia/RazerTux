"""Keylogger filter shared by the record helper and the recording logic (Ruling R-1).

What survives the filter is only the SET of distinct (source, ev_type, code) signals, each once,
in first-seen order: no values (a key counts only on press), no timestamps, no repeats.
On every device kind except mice, ordinary typing keys are never passed on; on mice the same holds
for nodes classified "keyboard" except the Naga-style number grid (``GRID_KEYS``). Dropped keys are
only counted as "standard keys seen" (number of distinct sources that delivered any, never presses
or keys). Sources are labels "if<N>:<node kind>".
"""
from __future__ import annotations

import time
from collections.abc import Callable

from evdev import ecodes as E

# Ordinary typing keys: KEY_ESC..KEY_COMPOSE (1-127) covers letters, digits, punctuation, space,
# enter, tab, backspace, modifiers, arrows, F1-F12, the keypad, navigation block and the
# international keys. Left out (they are extra keys worth recording): KEY_LINEFEED, KEY_MACRO,
# KEY_MUTE, KEY_VOLUMEDOWN, KEY_VOLUMEUP, KEY_POWER, KEY_SCALE.
_NOT_STANDARD = {E.KEY_LINEFEED, E.KEY_MACRO, E.KEY_MUTE, E.KEY_VOLUMEDOWN, E.KEY_VOLUMEUP,
                 E.KEY_POWER, E.KEY_SCALE}
# Further typing keys outside that block: keypad parentheses, phone-style numeric keys,
# currency keys.
_EXTRA_TYPING = ["KEY_KPLEFTPAREN", "KEY_KPRIGHTPAREN", "KEY_DOLLAR", "KEY_EURO",
                 *(f"KEY_NUMERIC_{n}" for n in range(10)), "KEY_NUMERIC_STAR", "KEY_NUMERIC_POUND",
                 "KEY_NUMERIC_A", "KEY_NUMERIC_B", "KEY_NUMERIC_C", "KEY_NUMERIC_D",
                 "KEY_NUMERIC_11", "KEY_NUMERIC_12"]
STANDARD_KEYS: frozenset[int] = frozenset(
    (set(range(E.KEY_ESC, E.KEY_COMPOSE + 1)) - _NOT_STANDARD)
    | {getattr(E, n) for n in _EXTRA_TYPING if hasattr(E, n)})

# Naga-style number grid: on a MOUSE these are side buttons, kept even on its keyboard node
GRID_KEYS: frozenset[int] = frozenset(
    {E.KEY_1, E.KEY_2, E.KEY_3, E.KEY_4, E.KEY_5, E.KEY_6, E.KEY_7, E.KEY_8, E.KEY_9, E.KEY_0,
     E.KEY_MINUS, E.KEY_EQUAL})

# Event types that can become a signal; EV_SYN, EV_MSC (MSC_SCAN carries the key's usage as value),
# EV_LED, EV_REP ... never do.
SIGNAL_TYPES = frozenset({E.EV_KEY, E.EV_REL, E.EV_ABS})


# Wheel axes keep the direction of the scroll (and nothing else: no magnitude); the hi-res twins
# duplicate every notch and are collapsed into the plain axes.
WHEEL_AXES = frozenset({E.REL_WHEEL, E.REL_HWHEEL})
HI_RES_AXES = frozenset({E.REL_WHEEL_HI_RES, E.REL_HWHEEL_HI_RES})


def classify_standard_key(code: int) -> bool:
    """True for an ordinary typing key (never recorded on keyboards and other non-mouse kinds)."""
    return code in STANDARD_KEYS


def filter_applies(kind: str | None) -> bool:
    """Standard keys are dropped for every kind but "mouse" (fail closed for unknown devices)."""
    return kind != "mouse"


def node_kind(source: str) -> str:
    """Node kind of a source label "if<N>:<node kind>" (discover._event_kind)."""
    return source.rsplit(":", 1)[-1]


def drops(kind: str | None, source: str, code: int) -> bool:
    """True if key ``code`` from ``source`` is an ordinary typing key that must not be recorded.

    Non-mouse kinds: every standard key. Mice: only on nodes classified "keyboard" (receivers
    with a paired keyboard), and there every standard key except the GRID_KEYS side buttons."""
    if not classify_standard_key(code):
        return False
    if filter_applies(kind):
        return True
    return node_kind(source) == "keyboard" and code not in GRID_KEYS


class SignalFilter:
    """Passes each recordable signal once. With ``repeat_after`` (seconds) a signal passes again
    when it was last passed at least that long ago: the record helper uses this so a button
    pressed once already (e.g. the click on "Start") can still be recorded in a later step."""

    def __init__(self, kind: str | None, repeat_after: float | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.kind = kind
        self._repeat_after, self._clock = repeat_after, clock
        self._seen: dict[tuple, float] = {}
        self._standard_sources: set[str] = set()

    def _new(self, sig: tuple) -> bool:
        now = self._clock()
        last = self._seen.get(sig)
        if last is not None and (self._repeat_after is None or now - last < self._repeat_after):
            return False
        self._seen[sig] = now
        return True

    def feed(self, source: str, ev_type: int, code: int, value: int) -> tuple | None:
        """The new signal (source, ev_type, code) to pass on, or None. Wheel axes carry a 4th
        element, the direction (+1 / -1)."""
        if ev_type not in SIGNAL_TYPES:
            return None
        if ev_type == E.EV_REL and code in HI_RES_AXES:
            return None
        if ev_type == E.EV_REL and code in WHEEL_AXES:
            if value == 0:
                return None
            sig = (source, ev_type, code, 1 if value > 0 else -1)
            return sig if self._new(sig) else None
        if ev_type == E.EV_KEY:
            if value != 1:  # release / autorepeat
                return None
            if drops(self.kind, source, code):
                self._standard_sources.add(source)
                return None
        sig = (source, ev_type, code)
        return sig if self._new(sig) else None

    @property
    def standard_keys_seen(self) -> int:
        """Number of distinct sources on which ordinary keys were pressed."""
        return len(self._standard_sources)
