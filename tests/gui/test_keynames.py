from evdev import ecodes as E
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent

from razertux.gui.keynames import combo_label, key_label, keycode_from_qt


def fake_key_event(native_scan_code):
    return QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.NoModifier,
                     native_scan_code, 0, 0, "z")


def test_keycode_from_qt_qwertz_z():
    # evdev codes are physical: the key right of T is KEY_Y, labelled "Z" on QWERTZ
    assert keycode_from_qt(fake_key_event(E.KEY_Y + 8)) == E.KEY_Y


def test_key_label_uses_layout():
    assert key_label(E.KEY_Y, layout="de") in ("Z", "z")
    assert key_label(E.KEY_Y, layout="us") in ("Y", "y")


def test_key_label_special_keys_german():
    assert key_label(E.KEY_ENTER, layout="de") == "Enter"
    assert key_label(E.KEY_LEFTCTRL, layout="de") == "Ctrl"
    assert key_label(E.KEY_LEFTSHIFT, layout="de") == "Shift"
    assert key_label(E.KEY_F5, layout="de") == "F5"


def test_key_label_fallback_to_evdev_name():
    assert key_label(E.BTN_LEFT, layout="de") == "Left Click"
    assert key_label(E.KEY_MICMUTE, layout="de")  # whatever, never empty
    assert key_label(999, layout="de") == "Key 999"


def test_combo_label():
    assert combo_label([E.KEY_LEFTCTRL, E.KEY_LEFTSHIFT, E.KEY_M], layout="de") == "Ctrl+Shift+M"


def test_sharp_s_not_uppercased_and_dead_keys_readable():
    assert key_label(E.KEY_MINUS, layout="de") == "ß"
    assert key_label(E.KEY_GRAVE, layout="de") == "Circumflex"
    assert key_label(E.KEY_EQUAL, layout="de") == "Acute"
