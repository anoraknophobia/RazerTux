"""Virtual output device ("naga-config virtual") the engine writes to."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from evdev import UInput
from evdev import ecodes as E

VIRTUAL_NAME = "naga-config virtual"

_RELS = [E.REL_X, E.REL_Y, E.REL_WHEEL, E.REL_HWHEEL, E.REL_WHEEL_HI_RES, E.REL_HWHEEL_HI_RES]


def _key_codes() -> list[int]:
    codes = []
    for code, names in E.KEY.items():
        names = names if isinstance(names, (list, tuple)) else [names]
        if 0 < code < E.KEY_MAX and any(n.startswith("KEY_") for n in names):
            codes.append(code)
    return codes


def capabilities() -> dict[int, list[int]]:
    keys = set(_key_codes()) | set(range(E.BTN_LEFT, E.BTN_TASK + 1))
    return {E.EV_KEY: sorted(keys), E.EV_REL: list(_RELS)}


class UinputOutput:
    """Output implementation backed by a uinput device."""

    def __init__(self, factory: Callable[..., Any] = UInput) -> None:
        self._ui = factory(events=capabilities(), name=VIRTUAL_NAME)

    def key(self, code: int, value: int) -> None:
        self._ui.write(E.EV_KEY, code, int(value))

    def rel(self, axis: int, value: int) -> None:
        self._ui.write(E.EV_REL, axis, int(value))

    def sync(self) -> None:
        self._ui.syn()

    def close(self) -> None:
        self._ui.close()
