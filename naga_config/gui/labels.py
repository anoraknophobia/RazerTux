"""Short German texts for actions and buttons."""
from __future__ import annotations

from naga_config.config.model import (
    Action, ComboAction, DefaultAction, DisabledAction, DpiStageAction,
    HypershiftAction, KeyAction, Macro, MacroAction,
)
from naga_config.gui.keynames import combo_label, key_label
from naga_config.hw.buttons import ButtonId

BUTTON_NAMES: dict[ButtonId, str] = {
    **{ButtonId(f"side_{n}"): f"Taste {n}" for n in range(1, 13)},
    ButtonId.LEFT: "Linksklick", ButtonId.RIGHT: "Rechtsklick",
    ButtonId.MIDDLE: "Mausrad-Klick",
    ButtonId.WHEEL_UP: "Rad hoch", ButtonId.WHEEL_DOWN: "Rad runter",
    ButtonId.WHEEL_LEFT: "Rad links", ButtonId.WHEEL_RIGHT: "Rad rechts",
    ButtonId.TOP_1: "Obere Taste vorn", ButtonId.TOP_2: "Obere Taste hinten",
}
WHEEL_BUTTONS = (ButtonId.WHEEL_UP, ButtonId.WHEEL_DOWN, ButtonId.WHEEL_LEFT, ButtonId.WHEEL_RIGHT)
_TOP_DEFAULT = {ButtonId.TOP_1: "DPI hoch", ButtonId.TOP_2: "DPI runter"}


def action_label(action: Action, button: ButtonId, macros: dict[str, Macro],
                 layout: str | None = None) -> str:
    match action:
        case DefaultAction():
            return _TOP_DEFAULT.get(button, "Standard")
        case DisabledAction():
            return "Aus"
        case KeyAction(code):
            return f"Taste {key_label(code, layout)}"
        case ComboAction(codes):
            return combo_label(codes, layout)
        case MacroAction(macro_id):
            m = macros.get(macro_id)
            return f"Makro: {m.name}" if m else "Makro: ? (fehlt)"
        case HypershiftAction():
            return "Hypershift"
        case DpiStageAction(direction):
            return "DPI hoch" if direction == "up" else "DPI runter"
    return "?"
