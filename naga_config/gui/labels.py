"""Short German texts for actions and buttons."""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from naga_config.config.model import (
    Action, ComboAction, DefaultAction, DisabledAction, DpiStageAction,
    HypershiftAction, KeyAction, Macro, MacroAction,
)
from naga_config.gui.keynames import combo_label, key_label
from naga_config.hw.buttons import ButtonId

def _tr(text: str) -> str:
    return QCoreApplication.translate("labels", text)


_BUTTON_NAMES: dict[ButtonId, str] = {
    ButtonId.LEFT: QT_TRANSLATE_NOOP("labels", "Linksklick"),
    ButtonId.RIGHT: QT_TRANSLATE_NOOP("labels", "Rechtsklick"),
    ButtonId.MIDDLE: QT_TRANSLATE_NOOP("labels", "Mausrad-Klick"),
    ButtonId.WHEEL_UP: QT_TRANSLATE_NOOP("labels", "Rad hoch"),
    ButtonId.WHEEL_DOWN: QT_TRANSLATE_NOOP("labels", "Rad runter"),
    ButtonId.WHEEL_LEFT: QT_TRANSLATE_NOOP("labels", "Rad links"),
    ButtonId.WHEEL_RIGHT: QT_TRANSLATE_NOOP("labels", "Rad rechts"),
    ButtonId.TOP_1: QT_TRANSLATE_NOOP("labels", "Obere Taste vorn"),
    ButtonId.TOP_2: QT_TRANSLATE_NOOP("labels", "Obere Taste hinten"),
}


def button_name(b: ButtonId) -> str:
    if b in _BUTTON_NAMES:
        return _tr(_BUTTON_NAMES[b])
    return _tr("Taste %1").replace("%1", b.value.removeprefix("side_"))


def battery_text(status: dict) -> str:
    """Battery for header and tray: percent with charging mark, "Kabel" when wired."""
    if status and not status.get("connected", True):
        return _tr("Maus nicht verbunden")
    if status and status.get("connection") == "wired":
        return _tr("Kabel")
    b = int(status.get("battery", -1)) if status else -1
    if b < 0:
        return _tr("Akku: –")
    text = _tr("Akku: %1 %").replace("%1", str(b))
    return text + " ⚡ " + _tr("lädt") if status.get("charging") else text


WHEEL_BUTTONS = (ButtonId.WHEEL_UP, ButtonId.WHEEL_DOWN, ButtonId.WHEEL_LEFT, ButtonId.WHEEL_RIGHT)


def action_label(action: Action, button: ButtonId, macros: dict[str, Macro],
                 layout: str | None = None) -> str:
    match action:
        case DefaultAction():
            if button == ButtonId.TOP_1:
                return _tr("DPI hoch")
            if button == ButtonId.TOP_2:
                return _tr("DPI runter")
            return _tr("Standard")
        case DisabledAction():
            return _tr("Aus")
        case KeyAction(code):
            return key_label(code, layout)
        case ComboAction(codes):
            return combo_label(codes, layout)
        case MacroAction(macro_id):
            m = macros.get(macro_id)
            if m:
                return _tr("Makro: %1").replace("%1", m.name)
            return _tr("Makro: ? (fehlt)")
        case HypershiftAction():
            return _tr("Hypershift")
        case DpiStageAction(direction):
            if direction == "cycle":
                return _tr("DPI-Stufe weiter")
            return _tr("DPI hoch") if direction == "up" else _tr("DPI runter")
    return "?"
