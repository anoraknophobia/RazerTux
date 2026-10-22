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
from PySide6.QtGui import QKeyEvent

MODIFIER_CODES = frozenset({
    E.KEY_LEFTCTRL, E.KEY_RIGHTCTRL, E.KEY_LEFTSHIFT, E.KEY_RIGHTSHIFT,
    E.KEY_LEFTALT, E.KEY_RIGHTALT, E.KEY_LEFTMETA, E.KEY_RIGHTMETA,
})

_MODIFIER_NAMES = {
    E.KEY_LEFTCTRL: "Strg", E.KEY_RIGHTCTRL: "Strg", E.KEY_LEFTSHIFT: "Shift",
    E.KEY_RIGHTSHIFT: "Shift", E.KEY_LEFTALT: "Alt", E.KEY_RIGHTALT: "Alt Gr",
    E.KEY_LEFTMETA: "Meta", E.KEY_RIGHTMETA: "Meta",
}
_SPECIAL_NAMES = {
    E.KEY_ENTER: "Enter", E.KEY_ESC: "Esc", E.KEY_BACKSPACE: "Rücktaste",
    E.KEY_TAB: "Tab", E.KEY_SPACE: "Leertaste", E.KEY_DELETE: "Entf",
    E.KEY_INSERT: "Einfg", E.KEY_HOME: "Pos1", E.KEY_END: "Ende",
    E.KEY_PAGEUP: "Bild hoch", E.KEY_PAGEDOWN: "Bild runter",
    E.KEY_UP: "Hoch", E.KEY_DOWN: "Runter", E.KEY_LEFT: "Links", E.KEY_RIGHT: "Rechts",
    E.KEY_CAPSLOCK: "Feststelltaste", E.KEY_SYSRQ: "Druck", E.KEY_SCROLLLOCK: "Rollen",
    E.KEY_PAUSE: "Pause", E.KEY_NUMLOCK: "Num", E.KEY_COMPOSE: "Menü",
}
_BUTTON_NAMES = {
    E.BTN_LEFT: "Linke Maustaste", E.BTN_RIGHT: "Rechte Maustaste",
    E.BTN_MIDDLE: "Mittlere Maustaste", E.BTN_SIDE: "Maustaste Seite",
    E.BTN_EXTRA: "Maustaste Extra",
}


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
            raise OSError(f"xkb-Keymap für Layout {layout!r} nicht ladbar")
        self._lib, self._state = lib, lib.xkb_state_new(keymap)

    def label(self, code: int) -> str:
        buf = ctypes.create_string_buffer(64)
        if self._lib.xkb_state_key_get_utf8(self._state, code + 8, buf, 64) > 0:
            text = buf.value.decode("utf-8", "replace")
            if text.isprintable() and not text.isspace():
                return text.upper() if len(text) == 1 else text
        sym = self._lib.xkb_state_key_get_one_sym(self._state, code + 8)
        if sym and self._lib.xkb_keysym_get_name(sym, buf, 64) > 0:
            return buf.value.decode("ascii", "replace")
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
        return f"Taste {code}"
    return name.split("_", 1)[-1].capitalize()


def key_label(code: int, layout: str | None = None) -> str:
    """Layout-correct keycap name for an evdev code (never empty)."""
    if code in _MODIFIER_NAMES:
        return _MODIFIER_NAMES[code]
    if code in _BUTTON_NAMES:
        return _BUTTON_NAMES[code]
    if code in _SPECIAL_NAMES:
        return _SPECIAL_NAMES[code]
    xkb = _xkb(layout or system_layout()) if 0 < code < 248 else None
    label = xkb.label(code) if xkb else ""
    return label or _evdev_name(code)


def combo_label(codes: list[int], layout: str | None = None) -> str:
    return "+".join(key_label(c, layout) for c in codes)
