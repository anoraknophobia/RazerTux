"""Mapper: turns button events into output according to the active profile."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from evdev import ecodes as E

from naga_config.config.model import (
    Action,
    ComboAction,
    DefaultAction,
    DisabledAction,
    DpiStageAction,
    HypershiftAction,
    KeyAction,
    Macro,
    MacroAction,
    MacroMode,
    Profile,
)
from naga_config.engine.macro import MacroPlayer
from naga_config.engine.output import Output
from naga_config.hw.buttons import ButtonId

logger = logging.getLogger(__name__)

_KEYS = [E.KEY_1, E.KEY_2, E.KEY_3, E.KEY_4, E.KEY_5, E.KEY_6, E.KEY_7,
         E.KEY_8, E.KEY_9, E.KEY_0, E.KEY_MINUS, E.KEY_EQUAL]

# Factory behaviour per button: a key/button code, or a wheel (axis, hi-res axis, sign).
DEFAULT_OUTPUT: dict[ButtonId, int | tuple[int, int, int]] = {
    **{ButtonId(f"side_{n}"): code for n, code in enumerate(_KEYS, start=1)},
    ButtonId.LEFT: E.BTN_LEFT,
    ButtonId.RIGHT: E.BTN_RIGHT,
    ButtonId.MIDDLE: E.BTN_MIDDLE,
    ButtonId.WHEEL_UP: (E.REL_WHEEL, E.REL_WHEEL_HI_RES, 1),
    ButtonId.WHEEL_DOWN: (E.REL_WHEEL, E.REL_WHEEL_HI_RES, -1),
    ButtonId.WHEEL_LEFT: (E.REL_HWHEEL, E.REL_HWHEEL_HI_RES, -1),
    ButtonId.WHEEL_RIGHT: (E.REL_HWHEEL, E.REL_HWHEEL_HI_RES, 1),
}
_WHEEL = {ButtonId.WHEEL_UP, ButtonId.WHEEL_DOWN, ButtonId.WHEEL_LEFT, ButtonId.WHEEL_RIGHT}
_DEFAULT_DPI = {ButtonId.TOP_1: "up", ButtonId.TOP_2: "down"}


class Mapper:
    def __init__(self, profile: Profile, macros: dict[str, Macro], out: Output,
                 on_dpi_stage: Callable[[str], None] = lambda d: None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._profile = profile
        self._macros = macros
        self._out = out
        self._on_dpi = on_dpi_stage
        self._player = MacroPlayer(out, sleep)
        self._active: dict[ButtonId, Action] = {}  # action chosen at press time
        self._held: list[int] = []                 # output codes currently down, press order
        self._hs_buttons: set[ButtonId] = set()

    def set_macros(self, macros: dict[str, Macro]) -> None:
        self._macros = macros

    async def set_profile(self, profile: Profile) -> None:
        await self.release_all()
        self._profile = profile

    async def release_all(self) -> None:
        if self._held:
            for code in reversed(self._held):
                self._out.key(code, 0)
            self._out.sync()
        self._held.clear()
        self._active.clear()
        self._hs_buttons.clear()
        await self._player.stop_all()

    async def handle(self, button: ButtonId, value: int) -> None:
        """value: 1=down, 0=up, 2=repeat. Wheel buttons: any non-zero is one tick."""
        if button in _WHEEL:
            if value:
                self._press_wheel(button)
            return
        if value == 1:
            if button in self._active:
                return
            layer = self._profile.hypershift if self._hs_buttons else self._profile.standard
            action = layer.get(button, DefaultAction())
            self._active[button] = action
            self._press(button, action)
        elif value == 0:
            action = self._active.pop(button, None)
            if action is not None:
                self._release(button, action)
        elif value == 2:
            action = self._active.get(button)
            if action is not None:
                self._repeat(button, action)

    # -- press / release / repeat -----------------------------------------

    def _codes(self, button: ButtonId, action: Action) -> list[int]:
        if isinstance(action, KeyAction):
            return [action.code]
        if isinstance(action, ComboAction):
            return list(action.codes)
        if isinstance(action, DefaultAction):
            code = DEFAULT_OUTPUT.get(button)
            return [code] if isinstance(code, int) else []
        return []

    def _press(self, button: ButtonId, action: Action) -> None:
        if isinstance(action, HypershiftAction):
            self._hs_buttons.add(button)
        elif isinstance(action, DpiStageAction):
            self._on_dpi(action.direction)
        elif isinstance(action, MacroAction):
            self._start_macro(button, action, action.mode)
        elif isinstance(action, DefaultAction) and button in _DEFAULT_DPI:
            self._on_dpi(_DEFAULT_DPI[button])
        else:
            self._key_batch(self._codes(button, action), 1)

    def _release(self, button: ButtonId, action: Action) -> None:
        if isinstance(action, HypershiftAction):
            self._hs_buttons.discard(button)
        elif isinstance(action, MacroAction):
            self._player.button_up(button)
        else:
            self._key_batch(list(reversed(self._codes(button, action))), 0)

    def _repeat(self, button: ButtonId, action: Action) -> None:
        codes = self._codes(button, action)
        if codes:
            self._out.key(codes[-1], 2)
            self._out.sync()

    def _key_batch(self, codes: list[int], value: int) -> None:
        if not codes:
            return
        for code in codes:
            self._out.key(code, value)
            if value:
                self._held.append(code)
            elif code in self._held:
                # remove the latest occurrence
                self._held.pop(len(self._held) - 1 - self._held[::-1].index(code))
        self._out.sync()

    def _press_wheel(self, button: ButtonId) -> None:
        action = self._profile.hypershift.get(button, DefaultAction()) \
            if self._hs_buttons else self._profile.standard.get(button, DefaultAction())
        if isinstance(action, (DisabledAction, HypershiftAction)):
            return
        if isinstance(action, DefaultAction):
            axis, hi_axis, sign = DEFAULT_OUTPUT[button]  # type: ignore[misc]
            self._out.rel(axis, sign)
            self._out.rel(hi_axis, sign * 120)
            self._out.sync()
        elif isinstance(action, (KeyAction, ComboAction)):
            codes = self._codes(button, action)
            for code in codes:
                self._out.key(code, 1)
            for code in reversed(codes):
                self._out.key(code, 0)
            self._out.sync()
        elif isinstance(action, DpiStageAction):
            self._on_dpi(action.direction)
        elif isinstance(action, MacroAction):
            self._start_macro(button, action, "toggle" if action.mode == "toggle" else "once")

    def _start_macro(self, button: ButtonId, action: MacroAction, mode: MacroMode) -> None:
        macro = self._macros.get(action.macro_id)
        if macro is None:
            logger.warning("Makro %r nicht gefunden (Taste %s)", action.macro_id, button)
            return
        self._player.start(macro, mode, action.count, button)
