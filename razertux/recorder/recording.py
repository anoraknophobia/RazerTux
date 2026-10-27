"""Button recording logic: guided (one step per button of a template) and free recording.

Qt-free. Fed with the listen events of the record helper (``signal`` triples); the keylogger rules
of ``keyfilter`` are applied again here. What is stored, per guided step and for the free recording:
the set of distinct signal names "if<N>:<node kind>:<code name>" (wheel axes with their direction
":+"/":-"), presses only, each once, in first-seen order; no values, no timing, no repeats and no
cursor motion (REL_X/REL_Y). Ordinary typing keys are never stored on any kind but mice; on mice
the digit row KEY_1..KEY_0, KEY_MINUS and KEY_EQUAL (the Naga number grid) are stored when they
come from a node that cannot type letters (the helper drops them on nodes with KEY_A), and keys of
non-keyboard nodes are stored as they are. Of dropped typing keys only the number of distinct
sources that sent any is kept (``standard_keys_seen``). Produces the ``buttons`` section of the
device report.
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from evdev import ecodes as E

from razertux.hw.buttons import ButtonId
from razertux.recorder.keyfilter import SIGNAL_TYPES, WHEEL_AXES, SignalFilter, classify_standard_key

__all__ = ["CHOICES", "TEMPLATE_CHOICES", "AUTO_STOP", "Step", "GuidedSteps", "SignalSet", "Recording",
           "classify_standard_key", "load_template", "signal_name"]

LAYOUTS = Path(__file__).resolve().parents[1] / "gui" / "assets" / "layouts"
# Template choices for mice the database does not know ("generic" = "I don't know")
TEMPLATE_CHOICES = {"ergo": "template_ergo", "ambi": "template_ambi", "compact": "template_compact",
                    "vertical": "template_vertical", "mmo": "template_mmo", "generic": "generic"}
CHOICES = tuple(TEMPLATE_CHOICES)
AUTO_STOP = 120.0  # seconds
ABS_MAX = 32  # EV_ABS signals kept per recording set
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
    return [Step(b, _hint(b, el)) for b, el in pairs]


_WHEEL_HINTS = {ButtonId.WHEEL_UP: "Scroll the wheel up", ButtonId.WHEEL_DOWN: "Scroll the wheel down",
                ButtonId.WHEEL_LEFT: "Tilt the wheel left", ButtonId.WHEEL_RIGHT: "Tilt the wheel right"}


def _hint(button: ButtonId, element: str) -> str:
    return _WHEEL_HINTS.get(button) or f"Press the {button.value.replace('_', ' ')} button (the {element} on the drawing)"


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
        self._items: list[tuple] = []

    def add(self, source: str, ev_type: int, code: int, sign: int | None = None) -> bool:
        """True if this is a new, recordable signal (presses only; typing keys are dropped).
        ``sign`` (+1/-1) is the direction of a wheel axis and is required for those."""
        if ev_type not in SIGNAL_TYPES or (ev_type, code) in _MOTION:
            return False
        if ev_type == E.EV_REL and code in WHEEL_AXES and sign not in (1, -1):
            return False
        if ev_type == E.EV_ABS and sum(1 for s in self._items if s[1] == E.EV_ABS) >= ABS_MAX:
            return False
        sig = self._filter.feed(source, ev_type, code, sign if ev_type == E.EV_REL else 1)
        if sig is None:
            return False
        self._items.append(sig)
        return True

    def contains(self, source: str, ev_type: int, code: int, sign: int | None = None) -> bool:
        return ((source, ev_type, code) if sign is None else (source, ev_type, code, sign)) in self._items

    def filtered(self, ev_type: int) -> list[tuple]:
        return [s for s in self._items if s[1] == ev_type]

    def names(self) -> list[str]:
        return [f"{i[0]}:{signal_name(i[1], i[2])}" + (("" if len(i) < 4 else ":+" if i[3] > 0 else ":-"))
                for i in self._items]

    @property
    def standard_keys_seen(self) -> int:
        return self._filter.standard_keys_seen

    @property
    def standard_sources(self) -> set[str]:
        return set(self._filter._standard_sources)


class Recording:
    """start / record / absent / next / finish; stops itself after AUTO_STOP seconds.

    ``steps`` None = free recording (everything goes to ``signals``). ``expired`` is time based:
    the caller must poll it (or call any method) to notice the auto-stop."""

    def __init__(self, kind: str | None, template: str, steps: list[Step] | None = None,
                 clock: Callable[[], float] = time.monotonic, limit: float = AUTO_STOP):
        self.kind, self.template, self.steps = kind, template, steps
        self._clock, self._limit = clock, limit
        self._t0: float | None = None
        self._reset()

    def _reset(self) -> None:
        self._index = 0
        self._results: dict[str, list[str] | str] = {}
        self._set = SignalSet(self.kind)  # signals of the current step (or the free recording)
        self._std_sources: set[str] = set()  # one set across the whole recording

    def start(self) -> None:
        self._reset()
        self._t0 = self._clock()

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

    @property
    def caught(self) -> list[str]:
        """Signal names of the current step (or of the free recording) so far."""
        return self._set.names()

    def _require_started(self) -> None:
        if self._t0 is None:
            raise RuntimeError("recording not started")

    def record(self, source: str, ev_type: int, code: int, sign: int | None = None) -> bool:
        self._require_started()
        if self.finished:
            return False
        ok = self._set.add(source, ev_type, code, sign)
        self._std_sources |= self._set.standard_sources
        return ok

    def _close_step(self, value: list[str] | str | None = None) -> None:
        step = self.current
        if step is not None:
            self._std_sources |= self._set.standard_sources
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

    def no_signal(self) -> None:
        """The current button gave no signal: drop what the step caught (e.g. the click on the
        wizard's own button with the recorded mouse) and move on."""
        self._require_started()
        if not self.finished:
            self._close_step([])

    def finish(self, standard_keys_seen: int = 0) -> dict:
        """The ``buttons`` report section. ``standard_keys_seen`` is the helper's source count."""
        self._require_started()
        results = dict(self._results)
        out: dict = {"template": self.template}
        step = self.current
        if step is not None:
            if self.expired:  # the step in progress was cut off: mark it, do not report it
                out["timed_out"] = True
                out["stopped_at"] = step.button.value
            elif self._set.names():
                results[step.button.value] = self._set.names()
        counts: dict[str, int] = {}
        for v in results.values():
            for n in (v if isinstance(v, list) else ()):
                counts[n] = counts.get(n, 0) + 1
        out["results"] = results
        out["duplicates"] = [n for n, c in counts.items() if c > 1]  # ambiguous for the importer
        out["signals"] = self._set.names() if self.steps is None else []
        out["standard_keys_seen"] = max(int(standard_keys_seen), len(self._std_sources | self._set.standard_sources))
        return out
