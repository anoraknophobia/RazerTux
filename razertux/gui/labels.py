"""Short German texts for actions and buttons."""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

from razertux.config.model import (
    Action, ComboAction, DefaultAction, DisabledAction, DpiStageAction,
    HypershiftAction, KeyAction, Macro, MacroAction,
)
from razertux.gui.keynames import combo_label, key_label
from razertux.hw.buttons import ButtonId

def _tr(text: str) -> str:
    return QCoreApplication.translate("labels", text)


_BUTTON_NAMES: dict[ButtonId, str] = {
    ButtonId.LEFT: QT_TRANSLATE_NOOP("labels", "Left Click"),
    ButtonId.RIGHT: QT_TRANSLATE_NOOP("labels", "Right Click"),
    ButtonId.MIDDLE: QT_TRANSLATE_NOOP("labels", "Scroll Click"),
    ButtonId.WHEEL_UP: QT_TRANSLATE_NOOP("labels", "Scroll Up"),
    ButtonId.WHEEL_DOWN: QT_TRANSLATE_NOOP("labels", "Scroll Down"),
    ButtonId.WHEEL_LEFT: QT_TRANSLATE_NOOP("labels", "Scroll Left"),
    ButtonId.WHEEL_RIGHT: QT_TRANSLATE_NOOP("labels", "Scroll Right"),
    ButtonId.TOP_1: QT_TRANSLATE_NOOP("labels", "Top button front"),
    ButtonId.TOP_2: QT_TRANSLATE_NOOP("labels", "Top button rear"),
    ButtonId.BACK: QT_TRANSLATE_NOOP("labels", "Side button rear"),
    ButtonId.FORWARD: QT_TRANSLATE_NOOP("labels", "Side button front"),
    ButtonId.DPI_UP: QT_TRANSLATE_NOOP("labels", "DPI up"),
    ButtonId.DPI_DOWN: QT_TRANSLATE_NOOP("labels", "DPI down"),
    ButtonId.CLUTCH: QT_TRANSLATE_NOOP("labels", "Sniper button"),
    ButtonId.PROFILE: QT_TRANSLATE_NOOP("labels", "Profile button"),
}
LEARN_NAME = QT_TRANSLATE_NOOP("labels", "Learned button %1")


def button_name(b: ButtonId) -> str:
    if b in _BUTTON_NAMES:
        return _tr(_BUTTON_NAMES[b])
    if b.value.startswith("learn_"):
        return _tr(LEARN_NAME).replace("%1", b.value.removeprefix("learn_"))
    return _tr("Button %1").replace("%1", b.value.removeprefix("side_"))


def battery_text(status: dict) -> str:
    """Battery for header and tray: percent with charging mark, "Kabel" when wired."""
    if status and not status.get("connected", True):
        return _tr("Mouse not connected")
    if status and status.get("connection") == "wired":
        return _tr("Wired")
    b = int(status.get("battery", -1)) if status else -1
    if b < 0:
        return _tr("Battery: –")
    text = _tr("Battery: %1 %").replace("%1", str(b))
    return text + " ⚡ " + _tr("charging") if status.get("charging") else text


# Top buttons while "Obere Tasten frei belegen" is off: the firmware handles them.
_FIRMWARE_TOP = {
    ButtonId.TOP_1: QT_TRANSLATE_NOOP("labels", "Scroll wheel stage cycle (mouse)"),
    ButtonId.TOP_2: QT_TRANSLATE_NOOP("labels", "Sensitivity stage cycle (mouse)"),
}


def firmware_top_label(button: ButtonId) -> str:
    return _tr(_FIRMWARE_TOP[button])


WHEEL_BUTTONS = (ButtonId.WHEEL_UP, ButtonId.WHEEL_DOWN, ButtonId.WHEEL_LEFT, ButtonId.WHEEL_RIGHT)


def action_label(action: Action, button: ButtonId, macros: dict[str, Macro],
                 layout: str | None = None) -> str:
    match action:
        case DefaultAction():
            if button == ButtonId.TOP_1:
                return _tr("DPI up")
            if button == ButtonId.TOP_2:
                return _tr("Sensitivity stage cycle")
            return _tr("Default")
        case DisabledAction():
            return _tr("Off")
        case KeyAction(code):
            return key_label(code, layout)
        case ComboAction(codes):
            return combo_label(codes, layout)
        case MacroAction(macro_id):
            m = macros.get(macro_id)
            if m:
                return _tr("Macro: %1").replace("%1", m.name)
            return _tr("Macro: ? (missing)")
        case HypershiftAction():
            return _tr("Hypershift")
        case DpiStageAction(direction):
            if direction == "cycle":
                return _tr("Sensitivity stage cycle")
            return _tr("DPI up") if direction == "up" else _tr("DPI down")
    return "?"
