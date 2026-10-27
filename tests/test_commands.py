"""Byte tests for razertux.razer.commands against OpenRazer razerchromacommon.c.

Expected values are derived from the C bodies (line refs are driver/razerchromacommon.c of
the imported OpenRazer tree), not from the comment blocks where those disagree.
"""
import json
from importlib import resources

import pytest

from razertux.razer.commands import BUILDERS, PARSERS

RED = (0xFF, 0x00, 0x00)
GREEN = (0x00, 0xFF, 0x00)


def pad(b: bytes, n: int) -> bytes:
    return b + bytes(n - len(b))


def test_every_builder_name_in_device_db_is_ported_or_documented():
    from razertux.razer.commands import NOT_PORTABLE

    data = json.loads(resources.files("razertux.devices").joinpath("openrazer_mice.json").read_text("utf-8"))
    names: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            if "steps" in o:
                names.update(s for s in o["steps"] if not s.startswith("raw:"))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    assert names - set(BUILDERS) == set(NOT_PORTABLE)
    assert not set(NOT_PORTABLE) & set(BUILDERS)


# (name, call args, expected (cls, id, args, size))   C line refs in comments
CASES = [
    # standard device functions
    ("razer_chroma_standard_set_device_mode", (0x03, 0x00), (0x00, 0x04, b"\x03\x00", 2)),
    ("razer_chroma_standard_set_device_mode", (0x02, 0x07), (0x00, 0x04, b"\x00\x00", 2)),  # 0x02 blocked, param forced 0
    ("razer_chroma_standard_get_device_mode", (), (0x00, 0x84, b"\x00\x00", 2)),
    ("razer_chroma_standard_get_serial", (), (0x00, 0x82, bytes(0x16), 0x16)),
    ("razer_chroma_standard_get_firmware_version", (), (0x00, 0x81, b"\x00\x00", 2)),
    # standard LED
    ("razer_chroma_standard_set_led_state", (0x01, 0x08, 0x01), (0x03, 0x00, b"\x01\x08\x01", 3)),  # l.103
    ("razer_chroma_standard_set_led_state", (0x01, 0x08, 0x09), (0x03, 0x00, b"\x01\x08\x01", 3)),  # clamp 0..1
    ("razer_chroma_standard_set_led_rgb", (0x01, 0x04, (1, 2, 3)), (0x03, 0x01, b"\x01\x04\x01\x02\x03", 5)),
    ("razer_chroma_standard_set_led_effect", (0x01, 0x04, 0x07), (0x03, 0x02, b"\x01\x04\x05", 3)),  # clamp 0..5
    ("razer_chroma_standard_set_led_brightness", (0x01, 0x05, 0x80), (0x03, 0x03, b"\x01\x05\x80", 3)),
    ("razer_chroma_standard_get_led_brightness", (0x01, 0x05), (0x03, 0x83, b"\x01\x05\x00", 3)),
    # standard matrix effects: class 0x03 id 0x0A, args[0] = MATRIX_EFFECT_* (razercommon.h)
    ("razer_chroma_standard_matrix_effect_none", (), (0x03, 0x0A, b"\x00", 1)),
    ("razer_chroma_standard_matrix_effect_wave", (0x02,), (0x03, 0x0A, b"\x01\x02", 2)),
    ("razer_chroma_standard_matrix_effect_wave", (0x00,), (0x03, 0x0A, b"\x01\x01", 2)),  # clamp 1..2
    ("razer_chroma_standard_matrix_effect_spectrum", (), (0x03, 0x0A, b"\x04", 1)),
    ("razer_chroma_standard_matrix_effect_reactive", (0x09, RED), (0x03, 0x0A, b"\x02\x04\xff\x00\x00", 5)),
    ("razer_chroma_standard_matrix_effect_static", (RED,), (0x03, 0x0A, b"\x06\xff\x00\x00", 4)),
    ("razer_chroma_standard_matrix_effect_breathing_random", (), (0x03, 0x0A, b"\x03\x03" + bytes(6), 8)),
    ("razer_chroma_standard_matrix_effect_breathing_single", (RED,), (0x03, 0x0A, b"\x03\x01\xff\x00\x00" + bytes(3), 8)),
    ("razer_chroma_standard_matrix_effect_breathing_dual", (RED, GREEN),
     (0x03, 0x0A, b"\x03\x02\xff\x00\x00\x00\xff\x00", 8)),
    # extended matrix (class 0x0F id 0x02): [store, led, effect, ...]
    ("razer_chroma_extended_matrix_effect_none", (0x01, 0x05), (0x0F, 0x02, b"\x01\x05\x00\x00\x00\x00", 6)),
    ("razer_chroma_extended_matrix_effect_static", (0x01, 0x05, RED),
     (0x0F, 0x02, b"\x01\x05\x01\x00\x00\x01\xff\x00\x00", 9)),
    ("razer_chroma_extended_matrix_effect_wave", (0x01, 0x05, 0x01), (0x0F, 0x02, b"\x01\x05\x04\x01\x28\x00", 6)),
    ("razer_chroma_extended_matrix_effect_wave", (0x01, 0x05, 0x09), (0x0F, 0x02, b"\x01\x05\x04\x02\x28\x00", 6)),
    ("razer_chroma_extended_matrix_effect_spectrum", (0x01, 0x05), (0x0F, 0x02, b"\x01\x05\x03\x00\x00\x00", 6)),
    ("razer_chroma_extended_matrix_effect_reactive", (0x01, 0x05, 0x02, RED),
     (0x0F, 0x02, b"\x01\x05\x05\x00\x02\x01\xff\x00\x00", 9)),
    ("razer_chroma_extended_matrix_effect_breathing_random", (0x01, 0x05), (0x0F, 0x02, b"\x01\x05\x02\x00\x00\x00", 6)),
    ("razer_chroma_extended_matrix_effect_breathing_single", (0x01, 0x05, RED),
     (0x0F, 0x02, b"\x01\x05\x02\x01\x00\x01\xff\x00\x00", 9)),
    ("razer_chroma_extended_matrix_effect_breathing_dual", (0x01, 0x05, RED, GREEN),
     (0x0F, 0x02, b"\x01\x05\x02\x02\x00\x02\xff\x00\x00\x00\xff\x00", 12)),
    ("razer_chroma_extended_matrix_brightness", (0x01, 0x04, 0xB7), (0x0F, 0x04, b"\x01\x04\xb7", 3)),
    ("razer_chroma_extended_matrix_get_brightness", (0x01, 0x04), (0x0F, 0x84, b"\x01\x04\x00", 3)),
    # mouse extended matrix (class 0x03 id 0x0D)
    ("razer_chroma_mouse_extended_matrix_effect_none", (0x01, 0x04), (0x03, 0x0D, b"\x01\x04\x00", 3)),
    ("razer_chroma_mouse_extended_matrix_effect_static", (0x01, 0x01, GREEN),
     (0x03, 0x0D, b"\x01\x01\x06\x00\xff\x00", 6)),
    ("razer_chroma_mouse_extended_matrix_effect_spectrum", (0x01, 0x04), (0x03, 0x0D, b"\x01\x04\x04", 3)),
    ("razer_chroma_mouse_extended_matrix_effect_reactive", (0x01, 0x01, 0x03, GREEN),
     (0x03, 0x0D, b"\x01\x01\x02\x03\x00\xff\x00", 7)),
    ("razer_chroma_mouse_extended_matrix_effect_breathing_random", (0x01, 0x01),
     (0x03, 0x0D, b"\x01\x01\x03\x03" + bytes(6), 10)),
    ("razer_chroma_mouse_extended_matrix_effect_breathing_single", (0x01, 0x01, GREEN),
     (0x03, 0x0D, b"\x01\x01\x03\x01\x00\xff\x00" + bytes(3), 10)),
    ("razer_chroma_mouse_extended_matrix_effect_breathing_dual", (0x01, 0x01, GREEN, RED),
     (0x03, 0x0D, b"\x01\x01\x03\x02\x00\xff\x00\xff\x00\x00", 10)),
    # misc
    ("razer_chroma_misc_get_battery_level", (), (0x07, 0x80, b"\x00\x00", 2)),
    ("razer_chroma_misc_get_charging_status", (), (0x07, 0x84, b"\x00\x00", 2)),
    ("razer_chroma_misc_get_polling_rate", (), (0x00, 0x85, b"\x00", 1)),
    ("razer_chroma_misc_set_polling_rate", (1000,), (0x00, 0x05, b"\x01", 1)),
    ("razer_chroma_misc_set_polling_rate", (500,), (0x00, 0x05, b"\x02", 1)),
    ("razer_chroma_misc_set_polling_rate", (125,), (0x00, 0x05, b"\x08", 1)),
    ("razer_chroma_misc_set_polling_rate", (777,), (0x00, 0x05, b"\x02", 1)),  # default 500 Hz
    ("razer_chroma_misc_get_polling_rate2", (), (0x00, 0xC0, b"\x00", 1)),
    ("razer_chroma_misc_set_polling_rate2", (4000, 0x00), (0x00, 0x40, b"\x00\x02", 2)),
    ("razer_chroma_misc_set_polling_rate2", (8000, 0x01), (0x00, 0x40, b"\x01\x01", 2)),
    ("razer_chroma_misc_set_polling_rate2", (2000, 0x00), (0x00, 0x40, b"\x00\x04", 2)),
    ("razer_chroma_misc_set_polling_rate2", (1000, 0x00), (0x00, 0x40, b"\x00\x08", 2)),
    ("razer_chroma_misc_set_polling_rate2", (500, 0x00), (0x00, 0x40, b"\x00\x10", 2)),
    ("razer_chroma_misc_set_polling_rate2", (250, 0x00), (0x00, 0x40, b"\x00\x20", 2)),
    ("razer_chroma_misc_set_polling_rate2", (125, 0x00), (0x00, 0x40, b"\x00\x40", 2)),
    ("razer_chroma_misc_set_polling_rate2", (3, 0x00), (0x00, 0x40, b"\x00\x10", 2)),  # default 500 Hz
    ("razer_chroma_misc_get_dock_brightness", (), (0x07, 0x82, b"\x00", 1)),
    ("razer_chroma_misc_set_dock_brightness", (0x40,), (0x07, 0x02, b"\x40", 1)),
    # razerchromacommon.c l.1008: get_razer_report(0x0E, 0x84, 0x02), arguments[0] = 0x01
    ("razer_chroma_misc_get_blade_brightness", (), (0x0E, 0x84, b"\x01\x00", 2)),
    # storage argument of set_dpi_xy is ignored by OpenRazer: always VARSTORE (l.1211)
    ("razer_chroma_misc_set_dpi_xy", (0x00, 1800, 1800), (0x04, 0x05, b"\x01\x07\x08\x07\x08\x00\x00", 7)),
    ("razer_chroma_misc_set_dpi_xy", (0x00, 50, 60000), (0x04, 0x05, b"\x01\x00\x64\xaf\xc8\x00\x00", 7)),  # clamp 100..45000
    ("razer_chroma_misc_get_dpi_xy", (0x00,), (0x04, 0x85, b"\x00" * 7, 7)),
    ("razer_chroma_misc_set_dpi_xy_byte", (0x1C, 0x2A), (0x04, 0x01, b"\x1c\x2a\x00", 3)),
    ("razer_chroma_misc_get_dpi_xy_byte", (), (0x04, 0x81, b"\x00\x00\x00", 3)),
    ("razer_chroma_misc_get_dpi_stages", (0x01,), (0x04, 0x86, pad(b"\x01", 0x26), 0x26)),
    ("razer_chroma_misc_get_idle_time", (), (0x07, 0x83, b"\x00\x00", 2)),
    ("razer_chroma_misc_set_idle_time", (300,), (0x07, 0x03, b"\x01\x2c", 2)),
    ("razer_chroma_misc_set_idle_time", (10,), (0x07, 0x03, b"\x00\x3c", 2)),  # clamp 60..900
    ("razer_chroma_misc_set_idle_time", (5000,), (0x07, 0x03, b"\x03\x84", 2)),
    ("razer_chroma_misc_get_low_battery_threshold", (), (0x07, 0x81, b"\x00", 1)),
    ("razer_chroma_misc_set_low_battery_threshold", (0x26,), (0x07, 0x01, b"\x26", 1)),
    ("razer_chroma_misc_set_low_battery_threshold", (0x00,), (0x07, 0x01, b"\x0c", 1)),  # clamp 0x0C..0x3F
    ("razer_chroma_misc_set_low_battery_threshold", (0xFF,), (0x07, 0x01, b"\x3f", 1)),
    # scroll wheel: storage argument is always VARSTORE
    ("razer_chroma_misc_set_scroll_mode", (1,), (0x02, 0x14, b"\x01\x01", 2)),
    ("razer_chroma_misc_get_scroll_mode", (), (0x02, 0x94, b"\x01\x00", 2)),
    ("razer_chroma_misc_set_scroll_acceleration", (True,), (0x02, 0x16, b"\x01\x01", 2)),
    ("razer_chroma_misc_set_scroll_acceleration", (False,), (0x02, 0x16, b"\x01\x00", 2)),
    ("razer_chroma_misc_get_scroll_acceleration", (), (0x02, 0x96, b"\x01\x00", 2)),
    ("razer_chroma_misc_set_scroll_smart_reel", (True,), (0x02, 0x17, b"\x01\x01", 2)),
    ("razer_chroma_misc_get_scroll_smart_reel", (), (0x02, 0x97, b"\x01\x00", 2)),
    # per-model effect builders
    ("razer_basilisk_mobile_effect_static", (RED,),
     (0x0F, 0x03, b"\x00\x00\x00\x00\x01\xff\x00\x00\xff\x00\x00", 0x0B)),
    ("razer_naga_trinity_effect_static", (RED,),
     (0x0F, 0x03, b"\x00\x00\x00\x00\x02" + b"\xff\x00\x00" * 3, 0x0E)),
    ("razer_naga_trinity_effect_breathing_single", (RED,),
     (0x0F, 0x02, b"\x00\x00\x02\x01\x00\x01\xff\x00\x00", 9)),
    ("razer_naga_trinity_effect_breathing_dual", (RED, GREEN),
     (0x0F, 0x02, b"\x00\x00\x02\x02\x00\x02\xff\x00\x00\x00\xff\x00", 12)),
    ("razer_naga_trinity_effect_breathing_random", (), (0x0F, 0x02, b"\x00\x00\x02\x00\x00\x00", 6)),
    ("razer_naga_trinity_effect_spectrum", (), (0x0F, 0x02, b"\x00\x00\x03\x00\x00\x00", 6)),
    ("razer_naga_trinity_effect_none", (), (0x0F, 0x02, b"\x00\x00\x00\x00\x00\x00", 6)),
]


@pytest.mark.parametrize("name,call,expected", CASES, ids=[f"{c[0]}-{i}" for i, c in enumerate(CASES)])
def test_builder_bytes(name, call, expected):
    cls, cid, args, size = BUILDERS[name](*call)
    ecls, eid, eargs, esize = expected
    assert (cls, cid, size) == (ecls, eid, esize)
    assert args == eargs
    assert isinstance(args, bytes)


def test_set_polling_rate_standard():
    assert BUILDERS["razer_chroma_misc_set_polling_rate"](1000) == (0x00, 0x05, b"\x01", 0x01)


def test_hyperpolling_rate_4000():
    cls, cid, args, size = BUILDERS["razer_chroma_misc_set_polling_rate2"](4000, 0x00)
    assert (cls, cid, size) == (0x00, 0x40, 0x02) and args == b"\x00\x02"


def test_naga_dpi_layout_unchanged():
    # The brief's example used storage 0x00 -> args[0] == 0x00, but the C code forces
    # VARSTORE (0x01); C wins. Payload bytes after the storage byte are the Naga's.
    assert BUILDERS["razer_chroma_misc_set_dpi_xy"](0x00, 1800, 1800)[2][:5] == bytes([1, 7, 8, 7, 8])


def test_dpi_stages_zero_based_ids_and_layout():
    # razer_chroma_misc_set_dpi_stages(store, count, active, dpi[]): ids are 0-based (l.1281)
    cls, cid, args, size = BUILDERS["razer_chroma_misc_set_dpi_stages"](0x01, 2, 1, [800, 800, 1800, 1800])
    assert (cls, cid, size) == (0x04, 0x06, 0x26)
    assert args == pad(
        bytes([1, 1, 2, 0, 0x03, 0x20, 0x03, 0x20, 0, 0, 1, 0x07, 0x08, 0x07, 0x08, 0, 0]), 0x26
    )


def test_dpi_stages_rejects_overlong():
    with pytest.raises(ValueError):
        BUILDERS["razer_chroma_misc_set_dpi_stages"](1, 12, 0, [800] * 24)


def test_args_never_exceed_report():
    for name, call, _ in CASES:
        assert len(BUILDERS[name](*call)[2]) <= 80


# ---- parsers (razermouse_driver.c read handlers) ----
def test_parser_keys_are_builder_keys():
    assert set(PARSERS) <= set(BUILDERS)
    for key in (
        "razer_chroma_misc_get_dpi_xy", "razer_chroma_misc_get_dpi_xy_byte", "razer_chroma_misc_get_dpi_stages",
        "razer_chroma_misc_get_polling_rate", "razer_chroma_misc_get_polling_rate2",
        "razer_chroma_misc_get_battery_level", "razer_chroma_misc_get_charging_status",
        "razer_chroma_misc_get_idle_time", "razer_chroma_misc_get_low_battery_threshold",
        "razer_chroma_extended_matrix_get_brightness", "razer_chroma_standard_get_led_brightness",
        "razer_chroma_misc_get_dock_brightness", "razer_chroma_misc_get_blade_brightness",
        "razer_chroma_misc_get_scroll_mode",
        "razer_chroma_misc_get_scroll_acceleration", "razer_chroma_misc_get_scroll_smart_reel",
        "razer_chroma_standard_get_device_mode", "razer_chroma_standard_get_firmware_version",
        "razer_chroma_standard_get_serial",
    ):
        assert key in PARSERS, key


def test_parse_dpi_xy_big_endian():
    assert PARSERS["razer_chroma_misc_get_dpi_xy"](bytes([0, 0x06, 0x40, 0x03, 0x20, 0, 0])) == (1600, 800)


def test_parse_dpi_xy_byte():
    assert PARSERS["razer_chroma_misc_get_dpi_xy_byte"](bytes([0x1C, 0x2A, 0])) == (0x1C, 0x2A)


def test_parse_dpi_stages():
    # driver comment l.3298: varstore, active, count, then (id, x BE, y BE, 2 reserved) per stage
    a = bytes([1, 2, 2, 1, 0x03, 0x20, 0x03, 0x20, 0, 0, 2, 0x07, 0x08, 0x07, 0x08, 0, 0])
    assert PARSERS["razer_chroma_misc_get_dpi_stages"](a) == (2, [(800, 800), (1800, 1800)])


def test_parse_dpi_stages_truncated_stops_at_data():
    a = bytes([1, 1, 3, 1, 0x03, 0x20, 0x03, 0x20, 0, 0, 2, 0x07])  # second entry cut off
    assert PARSERS["razer_chroma_misc_get_dpi_stages"](a) == (1, [(800, 800)])


@pytest.mark.parametrize("raw,hz", [(1, 1000), (2, 500), (8, 125), (3, 0)])
def test_parse_polling_rate(raw, hz):
    assert PARSERS["razer_chroma_misc_get_polling_rate"](bytes([raw])) == hz


@pytest.mark.parametrize("raw,hz", [(1, 8000), (2, 4000), (4, 2000), (8, 1000), (0x10, 500), (0x40, 125), (3, 0)])
def test_parse_polling_rate2(raw, hz):
    assert PARSERS["razer_chroma_misc_get_polling_rate2"](bytes([0, raw])) == hz


def test_parse_battery_charging_idle_threshold():
    assert PARSERS["razer_chroma_misc_get_battery_level"](bytes([0, 200])) == 200  # raw 0..255, as sysfs
    assert PARSERS["razer_chroma_misc_get_charging_status"](bytes([0, 1])) == 1
    assert PARSERS["razer_chroma_misc_get_idle_time"](bytes([0x01, 0x2C])) == 300
    assert PARSERS["razer_chroma_misc_get_low_battery_threshold"](bytes([0x26])) == 0x26


def test_parse_brightness():
    assert PARSERS["razer_chroma_extended_matrix_get_brightness"](bytes([1, 4, 0x80])) == 0x80
    assert PARSERS["razer_chroma_standard_get_led_brightness"](bytes([1, 5, 0x40])) == 0x40
    assert PARSERS["razer_chroma_misc_get_dock_brightness"](bytes([0x33])) == 0x33
    # razerkbd_driver.c razer_attr_read_matrix_brightness: arguments[1] for blade laptops
    assert PARSERS["razer_chroma_misc_get_blade_brightness"](bytes([1, 0x77])) == 0x77


def test_parse_scroll():
    assert PARSERS["razer_chroma_misc_get_scroll_mode"](bytes([1, 1])) == 1
    assert PARSERS["razer_chroma_misc_get_scroll_acceleration"](bytes([1, 0])) == 0
    assert PARSERS["razer_chroma_misc_get_scroll_smart_reel"](bytes([1, 1])) == 1


def test_parse_device_mode_firmware_serial():
    assert PARSERS["razer_chroma_standard_get_device_mode"](bytes([3, 0])) == (3, 0)
    assert PARSERS["razer_chroma_standard_get_firmware_version"](bytes([1, 7])) == "v1.7"
    serial = b"XX1234567890" + bytes(10)
    assert PARSERS["razer_chroma_standard_get_serial"](serial) == "XX1234567890"
