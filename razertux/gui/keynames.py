"""Key codes <-> readable, layout-aware names.

evdev key codes are physical positions. The label on the keycap depends on
the keyboard layout, which we resolve with libxkbcommon (ctypes). If the
library is missing, or a code has no xkb symbol, we fall back to the evdev
name. The layout is read from KDE's kxkbrc (first entry of LayoutList), the
env var NAGA_XKB_LAYOUT / XKB_DEFAULT_LAYOUT, else "de"; tests pass it
explicitly via the `layout` argument.
"""
from __future__ import annotations

import ctypes
import os
from functools import lru_cache
from pathlib import Path

from evdev import ecodes as E
from PySide6.QtCore import QCoreApplication, QT_TRANSLATE_NOOP
from PySide6.QtGui import QKeyEvent

MODIFIER_CODES = frozenset({
    E.KEY_LEFTCTRL, E.KEY_RIGHTCTRL, E.KEY_LEFTSHIFT, E.KEY_RIGHTSHIFT,
    E.KEY_LEFTALT, E.KEY_RIGHTALT, E.KEY_LEFTMETA, E.KEY_RIGHTMETA,
})

_MODIFIER_NAMES = {
    E.KEY_LEFTCTRL: QT_TRANSLATE_NOOP("keynames", "Ctrl"), E.KEY_RIGHTCTRL: QT_TRANSLATE_NOOP("keynames", "Ctrl"), E.KEY_LEFTSHIFT: QT_TRANSLATE_NOOP("keynames", "Shift"),
    E.KEY_RIGHTSHIFT: QT_TRANSLATE_NOOP("keynames", "Shift"), E.KEY_LEFTALT: QT_TRANSLATE_NOOP("keynames", "Alt"), E.KEY_RIGHTALT: QT_TRANSLATE_NOOP("keynames", "AltGr"),
    E.KEY_LEFTMETA: QT_TRANSLATE_NOOP("keynames", "Meta"), E.KEY_RIGHTMETA: QT_TRANSLATE_NOOP("keynames", "Meta"),
}
_SPECIAL_NAMES = {
    E.KEY_ENTER: QT_TRANSLATE_NOOP("keynames", "Enter"), E.KEY_ESC: QT_TRANSLATE_NOOP("keynames", "Esc"), E.KEY_BACKSPACE: QT_TRANSLATE_NOOP("keynames", "Backspace"),
    E.KEY_TAB: QT_TRANSLATE_NOOP("keynames", "Tab"), E.KEY_SPACE: QT_TRANSLATE_NOOP("keynames", "Space"), E.KEY_DELETE: QT_TRANSLATE_NOOP("keynames", "Del"),
    E.KEY_INSERT: QT_TRANSLATE_NOOP("keynames", "Ins"), E.KEY_HOME: QT_TRANSLATE_NOOP("keynames", "Home"), E.KEY_END: QT_TRANSLATE_NOOP("keynames", "End"),
    E.KEY_PAGEUP: QT_TRANSLATE_NOOP("keynames", "PgUp"), E.KEY_PAGEDOWN: QT_TRANSLATE_NOOP("keynames", "PgDn"),
    E.KEY_UP: QT_TRANSLATE_NOOP("keynames", "Up"), E.KEY_DOWN: QT_TRANSLATE_NOOP("keynames", "Down"), E.KEY_LEFT: QT_TRANSLATE_NOOP("keynames", "Left"), E.KEY_RIGHT: QT_TRANSLATE_NOOP("keynames", "Right"),
    E.KEY_CAPSLOCK: QT_TRANSLATE_NOOP("keynames", "Caps Lock"), E.KEY_SYSRQ: QT_TRANSLATE_NOOP("keynames", "Print"), E.KEY_SCROLLLOCK: QT_TRANSLATE_NOOP("keynames", "Scroll Lock"),
    E.KEY_PAUSE: QT_TRANSLATE_NOOP("keynames", "Pause"), E.KEY_NUMLOCK: QT_TRANSLATE_NOOP("keynames", "Num"), E.KEY_COMPOSE: QT_TRANSLATE_NOOP("keynames", "Menu"),
}
_BUTTON_NAMES = {
    E.BTN_LEFT: QT_TRANSLATE_NOOP("keynames", "Left Click"), E.BTN_RIGHT: QT_TRANSLATE_NOOP("keynames", "Right Click"),
    E.BTN_MIDDLE: QT_TRANSLATE_NOOP("keynames", "Middle Click"), E.BTN_SIDE: QT_TRANSLATE_NOOP("keynames", "Mouse Button 4"),
    E.BTN_EXTRA: QT_TRANSLATE_NOOP("keynames", "Mouse Button 5"),
}


def _tr(text: str) -> str:
    return QCoreApplication.translate("keynames", text)


_DEAD_KEYS = {
    "circumflex": QT_TRANSLATE_NOOP("keynames", "Circumflex"),
    "acute": QT_TRANSLATE_NOOP("keynames", "Acute"),
    "grave": QT_TRANSLATE_NOOP("keynames", "Grave"),
    "tilde": QT_TRANSLATE_NOOP("keynames", "Tilde"),
    "diaeresis": QT_TRANSLATE_NOOP("keynames", "Diaeresis"),
    "cedilla": QT_TRANSLATE_NOOP("keynames", "Cedilla"),
    "abovering": QT_TRANSLATE_NOOP("keynames", "Ring"),
}
_KP_NAMES = {
    "End": QT_TRANSLATE_NOOP("keynames", "End"), "Home": QT_TRANSLATE_NOOP("keynames", "Home"),
    "Prior": QT_TRANSLATE_NOOP("keynames", "PgUp"), "Next": QT_TRANSLATE_NOOP("keynames", "PgDn"),
    "Insert": QT_TRANSLATE_NOOP("keynames", "Ins"), "Delete": QT_TRANSLATE_NOOP("keynames", "Del"),
    "Left": QT_TRANSLATE_NOOP("keynames", "Left"), "Right": QT_TRANSLATE_NOOP("keynames", "Right"),
    "Up": QT_TRANSLATE_NOOP("keynames", "Up"), "Down": QT_TRANSLATE_NOOP("keynames", "Down"),
    "Begin": QT_TRANSLATE_NOOP("keynames", "Center"), "Add": "+", "Subtract": "-",
    "Multiply": "*", "Divide": "/", "Decimal": ",", "Enter": "Enter",
}


def _sym_label(name: str) -> str:
    """Readable name for a keysym that has no printable text."""
    if name.startswith("dead_"):
        base = name[5:]
        return _tr(_DEAD_KEYS[base]) if base in _DEAD_KEYS else base.capitalize()
    if name.startswith("KP_"):
        base = name[3:]
        return _tr("Num") + " " + (_tr(_KP_NAMES[base]) if base in _KP_NAMES else base)
    return name


def keycode_from_qt(event: QKeyEvent) -> int:
    """evdev code of a Qt key event (XKB keycodes are evdev + 8 on X11/Wayland)."""
    return event.nativeScanCode() - 8


def system_layout() -> str:
    for var in ("NAGA_XKB_LAYOUT", "XKB_DEFAULT_LAYOUT"):
        if os.environ.get(var):
            return os.environ[var].split(",")[0].strip()
    try:
        for line in (Path.home() / ".config" / "kxkbrc").read_text().splitlines():
            if line.startswith("LayoutList="):
                first = line.partition("=")[2].split(",")[0].strip()
                if first:
                    return first.split("(")[0]
    except OSError:
        pass
    return "de"


class _RuleNames(ctypes.Structure):
    _fields_ = [(n, ctypes.c_char_p) for n in ("rules", "model", "layout", "variant", "options")]


class _Xkb:
    def __init__(self, layout: str):
        lib = ctypes.CDLL("libxkbcommon.so.0")
        lib.xkb_context_new.restype = ctypes.c_void_p
        lib.xkb_context_new.argtypes = [ctypes.c_int]
        lib.xkb_keymap_new_from_names.restype = ctypes.c_void_p
        lib.xkb_keymap_new_from_names.argtypes = [ctypes.c_void_p, ctypes.POINTER(_RuleNames), ctypes.c_int]
        lib.xkb_state_new.restype = ctypes.c_void_p
        lib.xkb_state_new.argtypes = [ctypes.c_void_p]
        lib.xkb_state_key_get_utf8.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
        lib.xkb_state_key_get_one_sym.restype = ctypes.c_uint32
        lib.xkb_state_key_get_one_sym.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        lib.xkb_keysym_get_name.argtypes = [ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
        ctx = lib.xkb_context_new(0)
        names = _RuleNames(None, None, layout.encode(), None, None)
        keymap = lib.xkb_keymap_new_from_names(ctx, ctypes.byref(names), 0)
        if not ctx or not keymap:
            raise OSError(f"xkb keymap for layout {layout!r} cannot be loaded")
        self._lib, self._state = lib, lib.xkb_state_new(keymap)

    def label(self, code: int) -> str:
        buf = ctypes.create_string_buffer(64)
        if self._lib.xkb_state_key_get_utf8(self._state, code + 8, buf, 64) > 0:
            text = buf.value.decode("utf-8", "replace")
            if text.isprintable() and not text.isspace():
                up = text.upper()
                return up if len(text) == 1 and len(up) == 1 else text
        sym = self._lib.xkb_state_key_get_one_sym(self._state, code + 8)
        if sym and self._lib.xkb_keysym_get_name(sym, buf, 64) > 0:
            return _sym_label(buf.value.decode("ascii", "replace"))
        return ""


@lru_cache(maxsize=8)
def _xkb(layout: str) -> _Xkb | None:
    try:
        return _Xkb(layout)
    except (OSError, AttributeError):
        return None


def _evdev_name(code: int) -> str:
    name = E.KEY.get(code) or E.BTN.get(code)
    if isinstance(name, list):
        name = name[0]
    if not name:
        return _tr("Key %1").replace("%1", str(code))
    return name.split("_", 1)[-1].capitalize()


def key_label(code: int, layout: str | None = None) -> str:
    """Layout-correct keycap name for an evdev code (never empty)."""
    if code in _MODIFIER_NAMES:
        return _tr(_MODIFIER_NAMES[code])
    if code in _BUTTON_NAMES:
        return _tr(_BUTTON_NAMES[code])
    if code in _SPECIAL_NAMES:
        return _tr(_SPECIAL_NAMES[code])
    xkb = _xkb(layout or system_layout()) if 0 < code < 248 else None
    label = xkb.label(code) if xkb else ""
    return label or _evdev_name(code)


def combo_label(codes: list[int], layout: str | None = None) -> str:
    return "+".join(key_label(c, layout) for c in codes)
