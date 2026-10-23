"""OpenRazer command builders ported from driver/razerchromacommon.c.

Every builder has the signature of its C function (Python parameter names) and returns
``(cmd_class, cmd_id, args, size)``; ``args`` is zero-padded to ``size`` (the data_size
of the C report). Feed the tuple to ``report.build_report(..., txn=...)``; the
transaction id is a per-device property (``devices.db.Command.txn``), not part of a builder.

``PARSERS`` maps getter builder names to a function turning the response ``args`` into
what OpenRazer's sysfs read handler (razermouse_driver.c) reports.

Not ported (see ``NOT_PORTABLE``): builders that need a full 90-byte packet template.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

NOSTORE = 0x00
VARSTORE = 0x01

# MATRIX_EFFECT_* (razercommon.h)
_M_OFF, _M_WAVE, _M_REACTIVE, _M_BREATHING, _M_SPECTRUM, _M_STATIC = 0x00, 0x01, 0x02, 0x03, 0x04, 0x06

ARGS_MAX = 80

Rgb = tuple[int, int, int]
Built = tuple[int, int, bytes, int]

# Builders named in the device database that cannot be expressed as (class, id, args, size).
NOT_PORTABLE: dict[str, str] = {
    "razer_chroma_misc_set_orochi2011_led":
        "memcpy of a static 90-byte packet template whose status/remaining_packets/protocol "
        "header bytes are non-standard (01 00 00 06 48 ...); not a (class, id, args) command",
    "razer_chroma_misc_set_orochi2011_poll_dpi":
        "memcpy of a static 90-byte packet template with non-standard header bytes; "
        "not a (class, id, args) command",
}


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _u8(v: int) -> int:
    return int(v) & 0xFF


def _report(cls: int, cid: int, size: int, *, fill: dict[int, int] | None = None) -> Built:
    """get_razer_report + argument assignments (index -> byte)."""
    fill = fill or {}
    n = max(size, max(fill, default=-1) + 1)
    if n > ARGS_MAX:
        raise ValueError(f"args too long: {n} > {ARGS_MAX}")
    a = bytearray(n)
    for i, v in fill.items():
        a[i] = _u8(v)
    return cls, cid, bytes(a), size


def _rgb_at(fill: dict[int, int], i: int, rgb: Rgb) -> None:
    fill[i], fill[i + 1], fill[i + 2] = rgb


# -- standard device functions ---------------------------------------------

def razer_chroma_standard_set_device_mode(mode: int, param: int = 0) -> Built:
    if mode != 0x00 and mode != 0x03:  # 0x02 explicitly blocked
        mode = 0x00
    return _report(0x00, 0x04, 0x02, fill={0: mode, 1: 0x00})  # param is forced to 0x00


def razer_chroma_standard_get_device_mode() -> Built:
    return _report(0x00, 0x84, 0x02)


def razer_chroma_standard_get_serial() -> Built:
    return _report(0x00, 0x82, 0x16)


def razer_chroma_standard_get_firmware_version() -> Built:
    return _report(0x00, 0x81, 0x02)


# -- standard LED -------------------------------------------------------------

def razer_chroma_standard_set_led_state(variable_storage: int, led_id: int, led_state: int) -> Built:
    return _report(0x03, 0x00, 0x03, fill={0: variable_storage, 1: led_id, 2: _clamp(led_state, 0, 1)})


def razer_chroma_standard_set_led_rgb(variable_storage: int, led_id: int, rgb1: Rgb) -> Built:
    fill = {0: variable_storage, 1: led_id}
    _rgb_at(fill, 2, rgb1)
    return _report(0x03, 0x01, 0x05, fill=fill)


def razer_chroma_standard_set_led_effect(variable_storage: int, led_id: int, led_effect: int) -> Built:
    return _report(0x03, 0x02, 0x03, fill={0: variable_storage, 1: led_id, 2: _clamp(led_effect, 0, 5)})


def razer_chroma_standard_set_led_brightness(variable_storage: int, led_id: int, brightness: int) -> Built:
    return _report(0x03, 0x03, 0x03, fill={0: variable_storage, 1: led_id, 2: brightness})


def razer_chroma_standard_get_led_brightness(variable_storage: int, led_id: int) -> Built:
    return _report(0x03, 0x83, 0x03, fill={0: variable_storage, 1: led_id})


# -- standard matrix effects (class 0x03 id 0x0A) --------------------------

def _std_matrix(size: int, effect: int, fill: dict[int, int] | None = None) -> Built:
    f = {0: effect}
    f.update(fill or {})
    return _report(0x03, 0x0A, size, fill=f)


def razer_chroma_standard_matrix_effect_none() -> Built:
    return _std_matrix(0x01, _M_OFF)


def razer_chroma_standard_matrix_effect_wave(wave_direction: int) -> Built:
    return _std_matrix(0x02, _M_WAVE, {1: _clamp(wave_direction, 1, 2)})


def razer_chroma_standard_matrix_effect_spectrum() -> Built:
    return _std_matrix(0x01, _M_SPECTRUM)


def razer_chroma_standard_matrix_effect_reactive(speed: int, rgb1: Rgb) -> Built:
    fill = {1: _clamp(speed, 1, 4)}
    _rgb_at(fill, 2, rgb1)
    return _std_matrix(0x05, _M_REACTIVE, fill)


def razer_chroma_standard_matrix_effect_static(rgb1: Rgb) -> Built:
    fill: dict[int, int] = {}
    _rgb_at(fill, 1, rgb1)
    return _std_matrix(0x04, _M_STATIC, fill)


def razer_chroma_standard_matrix_effect_breathing_random() -> Built:
    return _std_matrix(0x08, _M_BREATHING, {1: 0x03})


def razer_chroma_standard_matrix_effect_breathing_single(rgb1: Rgb) -> Built:
    fill = {1: 0x01}
    _rgb_at(fill, 2, rgb1)
    return _std_matrix(0x08, _M_BREATHING, fill)


def razer_chroma_standard_matrix_effect_breathing_dual(rgb1: Rgb, rgb2: Rgb) -> Built:
    fill = {1: 0x02}
    _rgb_at(fill, 2, rgb1)
    _rgb_at(fill, 5, rgb2)
    return _std_matrix(0x08, _M_BREATHING, fill)


# -- extended matrix effects (class 0x0F id 0x02) --------------------------

def _ext_matrix(size: int, store: int, led: int, effect: int, fill: dict[int, int] | None = None) -> Built:
    f = {0: store, 1: led, 2: effect}
    f.update(fill or {})
    return _report(0x0F, 0x02, size, fill=f)


def razer_chroma_extended_matrix_effect_none(variable_storage: int, led_id: int) -> Built:
    return _ext_matrix(0x06, variable_storage, led_id, 0x00)


def razer_chroma_extended_matrix_effect_static(variable_storage: int, led_id: int, rgb: Rgb) -> Built:
    fill = {5: 0x01}
    _rgb_at(fill, 6, rgb)
    return _ext_matrix(0x09, variable_storage, led_id, 0x01, fill)


def razer_chroma_extended_matrix_effect_wave(variable_storage: int, led_id: int, direction: int) -> Built:
    return _ext_matrix(0x06, variable_storage, led_id, 0x04, {3: _clamp(direction, 0, 2), 4: 0x28})


def razer_chroma_extended_matrix_effect_spectrum(variable_storage: int, led_id: int) -> Built:
    return _ext_matrix(0x06, variable_storage, led_id, 0x03)


def razer_chroma_extended_matrix_effect_reactive(variable_storage: int, led_id: int, speed: int, rgb: Rgb) -> Built:
    fill = {4: _clamp(speed, 1, 4), 5: 0x01}
    _rgb_at(fill, 6, rgb)
    return _ext_matrix(0x09, variable_storage, led_id, 0x05, fill)


def razer_chroma_extended_matrix_effect_breathing_random(variable_storage: int, led_id: int) -> Built:
    return _ext_matrix(0x06, variable_storage, led_id, 0x02)


def razer_chroma_extended_matrix_effect_breathing_single(variable_storage: int, led_id: int, rgb1: Rgb) -> Built:
    fill = {3: 0x01, 5: 0x01}
    _rgb_at(fill, 6, rgb1)
    return _ext_matrix(0x09, variable_storage, led_id, 0x02, fill)


def razer_chroma_extended_matrix_effect_breathing_dual(variable_storage: int, led_id: int,
                                                       rgb1: Rgb, rgb2: Rgb) -> Built:
    fill = {3: 0x02, 5: 0x02}
    _rgb_at(fill, 6, rgb1)
    _rgb_at(fill, 9, rgb2)
    return _ext_matrix(0x0C, variable_storage, led_id, 0x02, fill)


def razer_chroma_extended_matrix_brightness(variable_storage: int, led_id: int, brightness: int) -> Built:
    return _report(0x0F, 0x04, 0x03, fill={0: variable_storage, 1: led_id, 2: brightness})


def razer_chroma_extended_matrix_get_brightness(variable_storage: int, led_id: int) -> Built:
    return _report(0x0F, 0x84, 0x03, fill={0: variable_storage, 1: led_id})


# -- mouse extended matrix effects (class 0x03 id 0x0D) --------------------

def _mouse_matrix(size: int, store: int, led: int, effect: int, fill: dict[int, int] | None = None) -> Built:
    f = {0: store, 1: led, 2: effect}
    f.update(fill or {})
    return _report(0x03, 0x0D, size, fill=f)


def razer_chroma_mouse_extended_matrix_effect_none(variable_storage: int, led_id: int) -> Built:
    return _mouse_matrix(0x03, variable_storage, led_id, 0x00)


def razer_chroma_mouse_extended_matrix_effect_static(variable_storage: int, led_id: int, rgb: Rgb) -> Built:
    fill: dict[int, int] = {}
    _rgb_at(fill, 3, rgb)
    return _mouse_matrix(0x06, variable_storage, led_id, 0x06, fill)


def razer_chroma_mouse_extended_matrix_effect_spectrum(variable_storage: int, led_id: int) -> Built:
    return _mouse_matrix(0x03, variable_storage, led_id, 0x04)


def razer_chroma_mouse_extended_matrix_effect_reactive(variable_storage: int, led_id: int,
                                                       speed: int, rgb: Rgb) -> Built:
    fill = {3: _clamp(speed, 1, 4)}
    _rgb_at(fill, 4, rgb)
    return _mouse_matrix(0x07, variable_storage, led_id, 0x02, fill)


def razer_chroma_mouse_extended_matrix_effect_breathing_random(variable_storage: int, led_id: int) -> Built:
    return _mouse_matrix(0x0A, variable_storage, led_id, 0x03, {3: 0x03})


def razer_chroma_mouse_extended_matrix_effect_breathing_single(variable_storage: int, led_id: int,
                                                               rgb1: Rgb) -> Built:
    fill = {3: 0x01}
    _rgb_at(fill, 4, rgb1)
    return _mouse_matrix(0x0A, variable_storage, led_id, 0x03, fill)


def razer_chroma_mouse_extended_matrix_effect_breathing_dual(variable_storage: int, led_id: int,
                                                             rgb1: Rgb, rgb2: Rgb) -> Built:
    fill = {3: 0x02}
    _rgb_at(fill, 4, rgb1)
    _rgb_at(fill, 7, rgb2)
    return _mouse_matrix(0x0A, variable_storage, led_id, 0x03, fill)


# -- misc ----------------------------------------------------------------------

def razer_chroma_misc_get_battery_level() -> Built:
    return _report(0x07, 0x80, 0x02)


def razer_chroma_misc_get_charging_status() -> Built:
    return _report(0x07, 0x84, 0x02)


def razer_chroma_misc_get_polling_rate() -> Built:
    return _report(0x00, 0x85, 0x01)


_POLL_BYTE = {1000: 0x01, 500: 0x02, 125: 0x08}


def razer_chroma_misc_set_polling_rate(polling_rate: int) -> Built:
    return _report(0x00, 0x05, 0x01, fill={0: _POLL_BYTE.get(polling_rate, 0x02)})  # default 500 Hz


def razer_chroma_misc_get_polling_rate2() -> Built:
    return _report(0x00, 0xC0, 0x01)


_POLL2_BYTE = {8000: 0x01, 4000: 0x02, 2000: 0x04, 1000: 0x08, 500: 0x10, 250: 0x20, 125: 0x40}


def razer_chroma_misc_set_polling_rate2(polling_rate: int, argument: int) -> Built:
    return _report(0x00, 0x40, 0x02, fill={0: argument, 1: _POLL2_BYTE.get(polling_rate, 0x10)})


def razer_chroma_misc_get_dock_brightness() -> Built:
    return _report(0x07, 0x82, 0x01)


def razer_chroma_misc_set_dock_brightness(brightness: int) -> Built:
    return _report(0x07, 0x02, 0x01, fill={0: brightness})


def razer_chroma_misc_set_dpi_xy(variable_storage: int, dpi_x: int, dpi_y: int) -> Built:
    """OpenRazer ignores ``variable_storage`` here and always writes VARSTORE."""
    dpi_x = _clamp(int(dpi_x) & 0xFFFF, 100, 45000)
    dpi_y = _clamp(int(dpi_y) & 0xFFFF, 100, 45000)
    return _report(0x04, 0x05, 0x07, fill={
        0: VARSTORE, 1: dpi_x >> 8, 2: dpi_x & 0xFF, 3: dpi_y >> 8, 4: dpi_y & 0xFF, 5: 0, 6: 0})


def razer_chroma_misc_get_dpi_xy(variable_storage: int) -> Built:
    return _report(0x04, 0x85, 0x07, fill={0: variable_storage})


def razer_chroma_misc_set_dpi_xy_byte(dpi_x: int, dpi_y: int) -> Built:
    return _report(0x04, 0x01, 0x03, fill={0: dpi_x, 1: dpi_y, 2: 0})


def razer_chroma_misc_get_dpi_xy_byte() -> Built:
    return _report(0x04, 0x81, 0x03)


def razer_chroma_misc_set_dpi_stages(variable_storage: int, count: int, active_stage: int,
                                     dpi: list[int] | tuple[int, ...]) -> Built:
    """``dpi`` is the flat C array [x1, y1, x2, y2, ...]; stage ids are 0-based as in OpenRazer."""
    if len(dpi) < 2 * count:
        raise ValueError("dpi needs 2 values per stage")
    fill = {0: variable_storage, 1: active_stage, 2: count}
    off = 3
    for i in range(count):
        x, y = int(dpi[2 * i]) & 0xFFFF, int(dpi[2 * i + 1]) & 0xFFFF
        fill.update({off: i, off + 1: x >> 8, off + 2: x & 0xFF, off + 3: y >> 8, off + 4: y & 0xFF,
                     off + 5: 0, off + 6: 0})
        off += 7
    return _report(0x04, 0x06, 0x26, fill=fill)


def razer_chroma_misc_get_dpi_stages(variable_storage: int) -> Built:
    return _report(0x04, 0x86, 0x26, fill={0: variable_storage})


def razer_chroma_misc_get_idle_time() -> Built:
    return _report(0x07, 0x83, 0x02)


def razer_chroma_misc_set_idle_time(idle_time: int) -> Built:
    idle_time = _clamp(int(idle_time) & 0xFFFF, 60, 900)
    return _report(0x07, 0x03, 0x02, fill={0: idle_time >> 8, 1: idle_time & 0xFF})


def razer_chroma_misc_get_low_battery_threshold() -> Built:
    return _report(0x07, 0x81, 0x01)


def razer_chroma_misc_set_low_battery_threshold(battery_threshold: int) -> Built:
    return _report(0x07, 0x01, 0x01, fill={0: _clamp(_u8(battery_threshold), 0x0C, 0x3F)})


# -- per-model effects -------------------------------------------------------

def razer_basilisk_mobile_effect_static(rgb: Rgb) -> Built:
    fill = {4: 0x01}
    _rgb_at(fill, 5, rgb)
    _rgb_at(fill, 8, rgb)
    return _report(0x0F, 0x03, 0x0B, fill=fill)


def razer_naga_trinity_effect_static(rgb: Rgb) -> Built:
    fill = {4: 0x02}
    for off in (5, 8, 11):
        _rgb_at(fill, off, rgb)
    return _report(0x0F, 0x03, 0x0E, fill=fill)


def razer_naga_trinity_effect_breathing_single(rgb: Rgb) -> Built:
    fill = {2: 0x02, 3: 0x01, 4: 0x00, 5: 0x01}
    _rgb_at(fill, 6, rgb)
    return _report(0x0F, 0x02, 0x09, fill=fill)


def razer_naga_trinity_effect_breathing_dual(rgb1: Rgb, rgb2: Rgb) -> Built:
    fill = {2: 0x02, 3: 0x02, 4: 0x00, 5: 0x02}
    _rgb_at(fill, 6, rgb1)
    _rgb_at(fill, 9, rgb2)
    return _report(0x0F, 0x02, 0x0C, fill=fill)


def razer_naga_trinity_effect_breathing_random() -> Built:
    return _report(0x0F, 0x02, 0x06, fill={0: 0, 1: 0, 2: 0x02})


def razer_naga_trinity_effect_spectrum() -> Built:
    return _report(0x0F, 0x02, 0x06, fill={0: 0, 1: 0, 2: 0x03})


def razer_naga_trinity_effect_none() -> Built:
    return _report(0x0F, 0x02, 0x06, fill={0: 0, 1: 0, 2: 0x00})


# -- scroll wheel (storage argument is always VARSTORE) --------------------

def razer_chroma_misc_set_scroll_mode(scroll_mode: int) -> Built:
    return _report(0x02, 0x14, 0x02, fill={0: VARSTORE, 1: scroll_mode})


def razer_chroma_misc_get_scroll_mode() -> Built:
    return _report(0x02, 0x94, 0x02, fill={0: VARSTORE})


def razer_chroma_misc_set_scroll_acceleration(acceleration: bool) -> Built:
    return _report(0x02, 0x16, 0x02, fill={0: VARSTORE, 1: bool(acceleration)})


def razer_chroma_misc_get_scroll_acceleration() -> Built:
    return _report(0x02, 0x96, 0x02, fill={0: VARSTORE})


def razer_chroma_misc_set_scroll_smart_reel(smart_reel: bool) -> Built:
    return _report(0x02, 0x17, 0x02, fill={0: VARSTORE, 1: bool(smart_reel)})


def razer_chroma_misc_get_scroll_smart_reel() -> Built:
    return _report(0x02, 0x97, 0x02, fill={0: VARSTORE})


# -- parsers (razermouse_driver.c read handlers) ------------------------------

_POLL_HZ = {0x01: 1000, 0x02: 500, 0x08: 125}
_POLL2_HZ = {0x01: 8000, 0x02: 4000, 0x04: 2000, 0x08: 1000, 0x10: 500, 0x40: 125}


def _parse_dpi_xy(a: bytes) -> tuple[int, int]:
    return (a[1] << 8) | a[2], (a[3] << 8) | a[4]


def _parse_dpi_stages(a: bytes) -> tuple[int, list[tuple[int, int]]]:
    """(active stage id as reported, [(x, y), ...]); stops at the end of the data like the driver."""
    stages: list[tuple[int, int]] = []
    for i in range(a[2]):
        o = 3 + 7 * i
        if o + 5 > len(a):
            break
        stages.append(((a[o + 1] << 8) | a[o + 2], (a[o + 3] << 8) | a[o + 4]))
    return a[1], stages


def _parse_serial(a: bytes) -> str:
    return a[:22].split(b"\x00")[0].decode("ascii", "replace")


PARSERS: dict[str, Callable[[bytes], Any]] = {
    "razer_chroma_misc_get_dpi_xy": _parse_dpi_xy,
    "razer_chroma_misc_get_dpi_xy_byte": lambda a: (a[0], a[1]),
    "razer_chroma_misc_get_dpi_stages": _parse_dpi_stages,
    "razer_chroma_misc_get_polling_rate": lambda a: _POLL_HZ.get(a[0], 0),    # 0 = unknown, as the driver
    "razer_chroma_misc_get_polling_rate2": lambda a: _POLL2_HZ.get(a[1], 0),
    "razer_chroma_misc_get_battery_level": lambda a: a[1],                    # raw 0..255, as sysfs
    "razer_chroma_misc_get_charging_status": lambda a: a[1],
    "razer_chroma_misc_get_idle_time": lambda a: (a[0] << 8) | a[1],
    "razer_chroma_misc_get_low_battery_threshold": lambda a: a[0],            # raw 0x0C..0x3F
    "razer_chroma_extended_matrix_get_brightness": lambda a: a[2],
    "razer_chroma_standard_get_led_brightness": lambda a: a[2],
    "razer_chroma_misc_get_dock_brightness": lambda a: a[0],
    "razer_chroma_misc_get_scroll_mode": lambda a: a[1],
    "razer_chroma_misc_get_scroll_acceleration": lambda a: a[1],
    "razer_chroma_misc_get_scroll_smart_reel": lambda a: a[1],
    "razer_chroma_standard_get_device_mode": lambda a: (a[0], a[1]),
    "razer_chroma_standard_get_firmware_version": lambda a: f"v{a[0]}.{a[1]}",
    "razer_chroma_standard_get_serial": _parse_serial,
}

BUILDERS: dict[str, Callable[..., Built]] = {
    name: fn for name, fn in list(globals().items())
    if name.startswith(("razer_chroma_", "razer_naga_", "razer_basilisk_")) and callable(fn)
}
