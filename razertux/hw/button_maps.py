"""Per-device button tables: (source, evdev type, code) -> ButtonId."""
from __future__ import annotations

from dataclasses import dataclass

from evdev import ecodes as E

from razertux.devices.db import DeviceInfo

from .buttons import _MOUSE_KEYS, _SIDE_KEYS, ButtonId, wheel_button


@dataclass(frozen=True)
class InputSignal:
    source: str
    ev_type: int
    code: int


ButtonMap = dict[InputSignal, ButtonId]


def _key_table(source: str, keys: dict[int, ButtonId]) -> ButtonMap:
    return {InputSignal(source, E.EV_KEY, code): b for code, b in keys.items()}


# Verified on hardware (identical to buttons.identify).
NAGA_V2_PRO: ButtonMap = {
    **_key_table("if02", _SIDE_KEYS),
    **_key_table("mouse", _MOUSE_KEYS),
}
# Naga V2 Pro side plates. UNVERIFIED ASSUMPTION (the plates are no longer available to
# test): the 6-key plate sends the same codes as keys 1-6 of the 12-key plate and the
# 2-key plate those of keys 1-2. If a key turns out different, "Taste anlernen" fixes it.
NAGA_V2_PRO_PLATES: dict[int, ButtonMap] = {
    plate: {sig: b for sig, b in NAGA_V2_PRO.items()
            if not b.value.startswith("side_") or int(b.value[5:]) <= plate}
    for plate in (12, 6, 2)
}
# Unverified: other Nagas presumably share the number-row side keys on if02.
NAGA_FAMILY: ButtonMap = _key_table("if02", _SIDE_KEYS)
GENERIC: ButtonMap = _key_table("mouse", {
    **_MOUSE_KEYS,
    E.BTN_SIDE: ButtonId.BACK,
    E.BTN_EXTRA: ButtonId.FORWARD,
})


def map_for(info: DeviceInfo, learned: dict[ButtonId, InputSignal], plate: int = 12) -> ButtonMap:
    if info.model == "naga_v2_pro":
        table = dict(NAGA_V2_PRO_PLATES.get(plate, NAGA_V2_PRO))
    elif info.model.startswith("naga"):
        table = {**NAGA_FAMILY, **GENERIC}
    else:
        table = dict(GENERIC)
    for button, signal in learned.items():
        table[signal] = button
    return table


def identify(button_map: ButtonMap, source: str, ev_type: int, code: int,
             value: int) -> ButtonId | None:
    """Map an evdev event to a button, or None if it is not a button event."""
    if ev_type == E.EV_KEY:
        return button_map.get(InputSignal(source, ev_type, code))
    if source == "mouse":
        return wheel_button(ev_type, code, value)
    return None
