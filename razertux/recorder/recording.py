"""Button recording logic: guided (one step per button of a template) and free recording.

Qt-free. Fed with the listen events of the record helper (``signal`` triples); the keylogger rules
of ``keyfilter`` are applied again here, so nothing but the SET of non-typing signals is kept: no
letters, digits, order, timing or values. Produces the ``buttons`` section of the device report.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from evdev import ecodes as E

from razertux.hw.buttons import ButtonId
from razertux.recorder.keyfilter import SIGNAL_TYPES, SignalFilter, classify_standard_key

__all__ = ["CHOICES", "TEMPLATE_CHOICES", "AUTO_STOP", "Step", "GuidedSteps", "SignalSet", "Recording",
           "classify_standard_key", "load_template", "signal_name"]

LAYOUTS = Path(__file__).resolve().parents[1] / "gui" / "assets" / "layouts"
# Template choices for mice the database does not know ("generic" = "I don't know")
TEMPLATE_CHOICES = {"ergo": "template_ergo", "ambi": "template_ambi", "compact": "template_compact",
                    "vertical": "template_vertical", "mmo": "template_mmo", "generic": "generic"}
CHOICES = tuple(TEMPLATE_CHOICES)
AUTO_STOP = 120.0  # seconds
_MOTION = {(E.EV_REL, E.REL_X), (E.EV_REL, E.REL_Y)}  # cursor movement is noise, not a button


def load_template(choice: str) -> list[tuple[ButtonId, str]]:
    """(button, element) pairs of a template choice, in layout file order."""
    if choice not in TEMPLATE_CHOICES:
        raise ValueError(f"unknown template {choice!r}")
    data = json.loads((LAYOUTS / f"{TEMPLATE_CHOICES[choice]}.json").read_text(encoding="utf-8"))
    return [(ButtonId(k), str(v["element"])) for k, v in data["buttons"].items()]


@dataclass(frozen=True)
class Step:
    button: ButtonId
    hint: str


def GuidedSteps(pairs: Iterable[tuple[ButtonId, str]]) -> list[Step]:
    """One step per (button, element) pair, in the given (label) order."""
    return [Step(b, f"Press the {b.value.replace('_', ' ')} button (the {el} on the drawing)")
            for b, el in pairs]


def signal_name(ev_type: int, code: int) -> str:
    tables = {E.EV_KEY: (E.KEY, E.BTN), E.EV_REL: (E.REL,), E.EV_ABS: (E.ABS,)}.get(ev_type, ())
    for t in tables:
        n = t.get(code)
        if isinstance(n, (list, tuple)):
            n = n[0] if n else None
        if n:
            return str(n)
    return f"CODE_{ev_type}_{code}"


class SignalSet:
    """Distinct (source, type, code) signals in first-seen order, for one device kind."""

    def __init__(self, kind: str | None):
        self.kind = kind
        self._filter = SignalFilter(kind)
        self._items: list[tuple[str, int, int]] = []

    def add(self, source: str, ev_type: int, code: int) -> bool:
        """True if this is a new, recordable signal (presses only; typing keys are dropped)."""
        if ev_type not in SIGNAL_TYPES or (ev_type, code) in _MOTION:
            return False
        sig = self._filter.feed(source, ev_type, code, 1)
        if sig is None:
            return False
        self._items.append(sig)
        return True

    def contains(self, source: str, ev_type: int, code: int) -> bool:
        return (source, ev_type, code) in self._items

    def filtered(self, ev_type: int) -> list[tuple[str, int, int]]:
        return [s for s in self._items if s[1] == ev_type]

    def names(self) -> list[str]:
        return [f"{s}:{signal_name(t, c)}" for s, t, c in self._items]

    @property
    def standard_keys_seen(self) -> int:
        return self._filter.standard_keys_seen


class Recording:
    """start / record / absent / next / finish; stops itself after AUTO_STOP seconds.

    ``steps`` None = free recording (everything goes to ``signals``)."""

    def __init__(self, kind: str | None, template: str, steps: list[Step] | None = None,
                 clock: Callable[[], float] = time.monotonic, limit: float = AUTO_STOP):
        self.kind, self.template, self.steps = kind, template, steps
        self._clock, self._limit = clock, limit
        self._t0: float | None = None
        self._index = 0
        self._results: dict[str, list[str] | str] = {}
        self._set: SignalSet = SignalSet(kind)  # signals of the current step (or the free recording)
        self._seen_std = 0

    def start(self) -> None:
        self._t0 = self._clock()
        self._index = 0
        self._set = SignalSet(self.kind)

    @property
    def expired(self) -> bool:
        return self._t0 is not None and self._clock() - self._t0 >= self._limit

    @property
    def current(self) -> Step | None:
        if self.steps is None or self._index >= len(self.steps):
            return None
        return self.steps[self._index]

    @property
    def finished(self) -> bool:
        return self._t0 is not None and (self.expired or (self.steps is not None and self.current is None))

    def _require_started(self) -> None:
        if self._t0 is None:
            raise RuntimeError("recording not started")

    def record(self, source: str, ev_type: int, code: int) -> bool:
        self._require_started()
        if self.finished:
            return False
        return self._set.add(source, ev_type, code)

    def _close_step(self, value: list[str] | str | None = None) -> None:
        step = self.current
        if step is not None:
            self._seen_std += self._set.standard_keys_seen
            self._results[step.button.value] = value if value is not None else self._set.names()
            self._index += 1
            self._set = SignalSet(self.kind)

    def next(self) -> None:
        """Keep what the current step caught (possibly nothing) and move on."""
        self._require_started()
        if not self.finished:
            self._close_step()

    def absent(self) -> None:
        """Mark the current button as not present on this mouse and move on."""
        self._require_started()
        if not self.finished:
            self._close_step("absent")

    def finish(self, standard_keys_seen: int = 0) -> dict:
        """The ``buttons`` report section. ``standard_keys_seen`` is the helper's source count."""
        self._require_started()
        results = dict(self._results)
        step = self.current
        if step is not None and (self._set.names() or self.expired):
            results[step.button.value] = self._set.names()  # step in progress when stopped
        free = self._set.names() if self.steps is None else []
        n = max(standard_keys_seen, self._seen_std + self._set.standard_keys_seen)
        return {"template": self.template, "results": results, "signals": free,
                "standard_keys_seen": [f"{n} input source(s)"] if n else []}
