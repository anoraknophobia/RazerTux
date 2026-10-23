"""Keylogger filter shared by the record helper and the recording logic (Ruling R-1).

What survives the filter is only the SET of distinct (source, ev_type, code) signals, each once,
in first-seen order: no values (a key counts only on press), no timestamps, no repeats.
On every device kind except mice, ordinary typing keys are never passed on; they are only counted
as "standard keys seen" (number of distinct sources that delivered any, never presses or keys).
"""
from __future__ import annotations

from evdev import ecodes as E

# Ordinary typing keys: KEY_ESC..KEY_COMPOSE (1-127) covers letters, digits, punctuation, space,
# enter, tab, backspace, modifiers, arrows, F1-F12, the keypad, navigation block and the
# international keys. Left out (they are extra keys worth recording): KEY_LINEFEED, KEY_MACRO,
# KEY_MUTE, KEY_VOLUMEDOWN, KEY_VOLUMEUP, KEY_POWER, KEY_SCALE.
_NOT_STANDARD = {E.KEY_LINEFEED, E.KEY_MACRO, E.KEY_MUTE, E.KEY_VOLUMEDOWN, E.KEY_VOLUMEUP,
                 E.KEY_POWER, E.KEY_SCALE}
STANDARD_KEYS: frozenset[int] = frozenset(set(range(E.KEY_ESC, E.KEY_COMPOSE + 1)) - _NOT_STANDARD)

# Event types that can become a signal; EV_SYN, EV_MSC (MSC_SCAN carries the key's usage as value),
# EV_LED, EV_REP ... never do.
SIGNAL_TYPES = frozenset({E.EV_KEY, E.EV_REL, E.EV_ABS})


def classify_standard_key(code: int) -> bool:
    """True for an ordinary typing key (never recorded on keyboards and other non-mouse kinds)."""
    return code in STANDARD_KEYS


def filter_applies(kind: str | None) -> bool:
    """Standard keys are dropped for every kind but "mouse" (fail closed for unknown devices)."""
    return kind != "mouse"


class SignalFilter:
    def __init__(self, kind: str | None):
        self.kind = kind
        self._drop_standard = filter_applies(kind)
        self._seen: set[tuple[str, int, int]] = set()
        self._standard_sources: set[str] = set()

    def feed(self, source: str, ev_type: int, code: int, value: int) -> tuple[str, int, int] | None:
        """The new signal (source, ev_type, code) to pass on, or None."""
        if ev_type not in SIGNAL_TYPES:
            return None
        if ev_type == E.EV_KEY:
            if value != 1:  # release / autorepeat
                return None
            if self._drop_standard and classify_standard_key(code):
                self._standard_sources.add(source)
                return None
        sig = (source, ev_type, code)
        if sig in self._seen:
            return None
        self._seen.add(sig)
        return sig

    @property
    def standard_keys_seen(self) -> int:
        """Number of distinct sources on which ordinary keys were pressed."""
        return len(self._standard_sources)
