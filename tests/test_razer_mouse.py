"""Generic RazerMouse driver: commands from the device database (Task 3).

Expected bytes are derived from OpenRazer driver/razermouse_driver.c (sysfs handlers)
and driver/razerchromacommon.c (builders); see the comments per test.
"""
import pytest
from fake_transport import FakeTransport

from razertux.config.model import LightingZone
from razertux.devices.db import Command, DeviceInfo, by_pid, load_devices
from razertux.razer import commands as CMD
from razertux.razer.mouse import (
    EFFECT_PATTERNS,
    NOSTORE,
    VARSTORE,
    RazerMouse,
    UnsupportedFeature,
    make_mouse,
)
from razertux.razer.naga_v2_pro import NagaV2Pro

STATE, RGB, LEFF = ("razer_chroma_standard_set_led_state", "razer_chroma_standard_set_led_rgb",
                    "razer_chroma_standard_set_led_effect")
EXT = "razer_chroma_extended_matrix_effect_"
MEXT = "razer_chroma_mouse_extended_matrix_effect_"


def cmd(txn, *steps):
    return Command(next(s for s in steps if not s.startswith("raw:")), txn, tuple(steps))


def breath(prefix, txn):
    return cmd(txn, prefix + "breathing_single", prefix + "breathing_dual", prefix + "breathing_random")


def make_info(txn=0x3F, battery=False, model="test_mouse", dpi_max=16000, extra=None, drop=()):
    """Synthetic device: 0x3F without battery (wired, older), 0x1F with battery."""
    p = {
        "get_firmware": cmd(txn, "razer_chroma_standard_get_firmware_version"),
        "get_serial": cmd(txn, "razer_chroma_standard_get_serial"),
        "get_dpi": cmd(txn, "razer_chroma_misc_get_dpi_xy"),
        "set_dpi": cmd(txn, "razer_chroma_misc_set_dpi_xy"),
        "get_dpi_stages": cmd(txn, "razer_chroma_misc_get_dpi_stages"),
        "set_dpi_stages": cmd(txn, "razer_chroma_misc_set_dpi_stages"),
        "get_poll_rate": cmd(txn, "razer_chroma_misc_get_polling_rate"),
        "set_poll_rate": cmd(txn, "razer_chroma_misc_set_polling_rate"),
        "get_brightness:logo": cmd(txn, "razer_chroma_extended_matrix_get_brightness"),
        "set_brightness:logo": cmd(txn, "razer_chroma_extended_matrix_brightness"),
        "set_effect:logo:none": cmd(txn, EXT + "none"),
        "set_effect:logo:static": cmd(txn, EXT + "static"),
        "set_effect:logo:breath": breath(EXT, txn),
        "set_effect:logo:spectrum": cmd(txn, EXT + "spectrum"),
        "set_effect:logo:wave": cmd(txn, EXT + "wave"),
        "set_effect:logo:reactive": cmd(txn, EXT + "reactive"),
        "get_brightness:scroll": cmd(txn, "razer_chroma_standard_get_led_brightness"),
        "set_brightness:scroll": cmd(txn, "razer_chroma_standard_set_led_brightness"),
        "set_effect:scroll:none": cmd(txn, STATE),
        "set_effect:scroll:on": cmd(txn, STATE),
        "set_effect:scroll:static": cmd(txn, STATE, RGB, LEFF),
        "set_effect:scroll:breath": cmd(txn, STATE, RGB, LEFF),
        "set_effect:scroll:blinking": cmd(txn, STATE, RGB, LEFF),
        "set_effect:scroll:spectrum": cmd(txn, STATE, LEFF),
        "get_scroll_mode": cmd(txn, "razer_chroma_misc_get_scroll_mode"),
        "set_scroll_mode": cmd(txn, "razer_chroma_misc_set_scroll_mode"),
        "get_scroll_acceleration": cmd(txn, "razer_chroma_misc_get_scroll_acceleration"),
        "set_scroll_acceleration": cmd(txn, "razer_chroma_misc_set_scroll_acceleration"),
        "get_scroll_smart_reel": cmd(txn, "razer_chroma_misc_get_scroll_smart_reel"),
        "set_scroll_smart_reel": cmd(txn, "razer_chroma_misc_set_scroll_smart_reel"),
    }
    features = {"firmware", "serial", "dpi", "dpi_stages", "poll_rate",
                "scroll_mode", "scroll_acceleration", "scroll_smart_reel"}
    zones = {"logo": ("breath", "none", "reactive", "spectrum", "static", "wave"),
             "scroll": ("blinking", "breath", "none", "on", "spectrum", "static")}
    if battery:
        p |= {
            "get_battery": cmd(txn, "razer_chroma_misc_get_battery_level"),
            "get_charging": cmd(txn, "razer_chroma_misc_get_charging_status"),
            "get_idle_time": cmd(txn, "razer_chroma_misc_get_idle_time"),
            "set_idle_time": cmd(txn, "razer_chroma_misc_set_idle_time"),
            "get_low_battery_threshold": cmd(txn, "razer_chroma_misc_get_low_battery_threshold"),
            "set_low_battery_threshold": cmd(txn, "razer_chroma_misc_set_low_battery_threshold"),
        }
        features |= {"battery", "charging", "idle_time", "low_battery_threshold"}
    p |= extra or {}
    for k in drop:
        p.pop(k, None)
    for k in p:
        if k.startswith("set_effect:"):
            z = k.split(":")[1]
            zones.setdefault(z, ())
    return DeviceInfo(model=model, name="Test", pids={0x1234: "wired"}, dpi_max=dpi_max,
                      poll_rates=(125, 500, 1000), features=frozenset(features),
                      zones=zones, protocol=p)


class _Echo(dict):
    """Responses: echo the request args for every command without an explicit entry."""

    def __contains__(self, key):
        return True

    def __missing__(self, key):
        return lambda a: a


def echo(responses=None):
    t = FakeTransport()
    t.responses = _Echo(responses or {})
    return t


def Z(effect, color=(1, 2, 3)):
    return LightingZone(effect, color, 100)


def packets(t):
    return [(r.cmd_class, r.cmd_id, r.size, bytes(r.args[:r.size])) for r in t.requests]


# -- Step 1 of the brief ------------------------------------------------------

def test_txn_from_database():
    for txn, bat in ((0x3F, False), (0x1F, True)):
        info = make_info(txn, battery=bat)
        t = echo()
        RazerMouse(info, t).set_dpi(800, 800)
        assert t.raw[0][1] == info.protocol["set_dpi"].txn == txn


def test_txn_per_command_not_per_device():
    info = make_info(0x3F, extra={"get_poll_rate": cmd(0xFF, "razer_chroma_misc_get_polling_rate")})
    t = echo({(0x00, 0x85): bytes([0x01]), (0x00, 0x81): bytes([1, 0])})
    m = RazerMouse(info, t)
    assert m.poll_rate() == 1000
    m.firmware()
    assert [r[1] for r in t.raw] == [0xFF, 0x3F]


def test_unsupported_feature_raises_and_sends_nothing():
    t = echo()
    m = RazerMouse(make_info(0x3F, battery=False), t)
    assert not m.supports("battery")
    for call in (m.battery, m.charging, m.idle_time, m.low_battery_threshold,
                 lambda: m.set_idle_time(300), lambda: m.set_low_battery_threshold(5),
                 lambda: m.brightness("left"), lambda: m.set_effect("logo", Z("blinking"))):
        with pytest.raises(UnsupportedFeature):
            call()
    assert t.sent == []


def test_make_mouse_returns_naga_subclass_for_naga():
    devs = load_devices()
    t = FakeTransport.full()
    assert type(make_mouse(devs["naga_v2_pro"], t)) is NagaV2Pro
    assert isinstance(make_mouse(devs["naga_v2_pro"], t), RazerMouse)
    assert type(make_mouse(devs["viper"], t)) is RazerMouse
    m = make_mouse(by_pid(0x00A8), t, pid=0x00A8)
    assert type(m) is NagaV2Pro and m.info.model == "naga_v2_pro"


def test_make_mouse_flattens_for_pid():
    # a model whose PIDs differ (Ruling M3): the per-PID entries become visible
    devs = load_devices()
    model = next(d for d in devs.values() if d.pid_protocol)
    pid, extra = next(iter(model.pid_protocol.items()))
    m = make_mouse(model, FakeTransport(), pid=pid)
    assert m.info.pid_protocol == {}
    for k, c in extra.items():
        assert m.info.protocol[k] == c


def test_naga_keeps_old_constructor():
    t = FakeTransport.full()
    n = NagaV2Pro(t)
    assert n.info.model == "naga_v2_pro"
    assert n.dpi() == (800, 800)


# -- executability (Rulings M1/M2) --------------------------------------------

def test_unknown_builder_is_unsupported():
    info = make_info(extra={"set_dpi": cmd(0x3F, "razer_chroma_misc_set_dpi_from_the_future"),
                            "get_battery": cmd(0x3F, "razer_some_unknown_getter")},
                     drop=())
    info = DeviceInfo(**{**info.__dict__, "features": info.features | {"battery"}})
    t = echo()
    m = RazerMouse(info, t)
    assert m.supports("dpi") and not m.supports("set_dpi")
    assert not m.supports("battery")
    with pytest.raises(UnsupportedFeature):
        m.set_dpi(800, 800)
    with pytest.raises(UnsupportedFeature):
        m.battery()
    assert t.sent == []


def test_raw_step_is_unsupported():
    info = make_info(extra={"set_effect:matrix:static":
                            cmd(0x1F, "raw:0x0f/0x02/0x06", "razer_naga_trinity_effect_static")})
    t = echo()
    m = RazerMouse(info, t)
    assert not m.supports("set_effect:matrix:static")
    with pytest.raises(UnsupportedFeature):
        m.set_effect("matrix", Z("static"))
    assert t.sent == []


def test_not_portable_builder_is_unsupported():
    info = make_info(extra={"set_dpi": cmd(0xFF, "razer_chroma_misc_set_orochi2011_poll_dpi"),
                            "set_effect:logo:none": cmd(0xFF, "razer_chroma_misc_set_orochi2011_led")})
    m = RazerMouse(info, echo())
    assert not m.supports("set_dpi")
    assert not m.supports("set_effect:logo:off")


def test_feature_needs_database_feature_and_command():
    info = make_info(battery=True)
    info = DeviceInfo(**{**info.__dict__, "features": info.features - {"charging"}})
    m = RazerMouse(info, echo())
    assert m.supports("battery") and not m.supports("charging")
    assert not m.supports("no_such_feature")


def test_dpi_max_zero_hides_dpi():
    t = echo()
    m = RazerMouse(make_info(dpi_max=0), t)
    for f in ("dpi", "set_dpi", "dpi_stages", "set_dpi_stages"):
        assert not m.supports(f)
    with pytest.raises(UnsupportedFeature):
        m.dpi()
    with pytest.raises(UnsupportedFeature):
        m.set_dpi_stages(1, [(800, 800)])
    assert t.sent == []


def test_abyssus_from_database_has_no_dpi():
    m = make_mouse(load_devices()["abyssus"], FakeTransport())
    assert not m.supports("dpi") and not m.supports("dpi_stages")


# -- DPI ------------------------------------------------------------------------

def test_set_dpi_always_varstore_and_persistent():
    # razer_chroma_misc_set_dpi_xy writes VARSTORE whatever the caller passes
    t = echo()
    RazerMouse(make_info(), t).set_dpi(1800, 900)
    assert packets(t) == [(0x04, 0x05, 0x07, bytes([VARSTORE, 0x07, 0x08, 0x03, 0x84, 0, 0]))]
    assert RazerMouse.PERSISTENT_COMMANDS["set_dpi"] is True
    assert {"set_dpi", "set_dpi_stages", "set_poll_rate", "set_idle_time",
            "set_low_battery_threshold"} <= set(RazerMouse.PERSISTENT_COMMANDS)


def test_set_dpi_range_from_database():
    t = echo()
    m = RazerMouse(make_info(dpi_max=16000), t)
    m.set_dpi(16000, 100)
    for bad in (99, 16001, 800.0):
        with pytest.raises(ValueError):
            m.set_dpi(bad, 800)
    assert len(t.sent) == 1


def test_dpi_reads_nostore():
    # razer_attr_read_dpi: get_dpi_xy(NOSTORE) for almost every model
    t = echo({(0x04, 0x85): bytes([0, 0x06, 0x40, 0x03, 0x20, 0, 0])})
    assert RazerMouse(make_info(), t).dpi() == (1600, 800)
    assert t.requests[0].args[0] == NOSTORE and t.requests[0].size == 7


@pytest.mark.parametrize("model", ["orochi_2013", "imperator"])
def test_dpi_reads_varstore_for_orochi_2013_and_imperator(model):
    # razer_attr_read_dpi: OROCHI_2013 and IMPERATOR use get_dpi_xy(VARSTORE)
    t = echo({(0x04, 0x85): bytes([1, 0x03, 0x20, 0x03, 0x20, 0, 0])})
    RazerMouse(make_info(model=model), t).dpi()
    assert t.requests[0].args[0] == VARSTORE


def test_byte_dpi_is_unsupported():
    # get/set_dpi_xy_byte carry raw bytes whose DPI meaning the driver does not define
    info = make_info(extra={"get_dpi": cmd(0xFF, "razer_chroma_misc_get_dpi_xy_byte"),
                            "set_dpi": cmd(0xFF, "razer_chroma_misc_set_dpi_xy_byte")})
    m = RazerMouse(info, echo())
    assert not m.supports("dpi") and not m.supports("set_dpi")


def test_dpi_stages_generic_layout():
    # razer_chroma_misc_set_dpi_stages: 0-based stage ids, active as given (1-based)
    t = echo()
    RazerMouse(make_info(), t).set_dpi_stages(2, [(800, 800), (1600, 1600)])
    cls, cid, size, args = packets(t)[0]
    assert (cls, cid, size) == (0x04, 0x06, 0x26)
    assert args[:17] == bytes([NOSTORE, 2, 2, 0, 0x03, 0x20, 0x03, 0x20, 0, 0,
                               1, 0x06, 0x40, 0x06, 0x40, 0, 0])


def test_dpi_stages_generic_read():
    stage = lambda i, d: bytes([i, d >> 8, d & 0xFF, d >> 8, d & 0xFF, 0, 0])
    t = echo({(0x04, 0x86): bytes([1, 2, 2]) + stage(0, 800) + stage(1, 1600)})
    assert RazerMouse(make_info(), t).dpi_stages() == (2, [(800, 800), (1600, 1600)])
    assert t.requests[0].args[0] == VARSTORE and t.requests[0].size == 0x26


@pytest.mark.parametrize("active,stages", [(1, []), (0, [(800, 800)]), (2, [(800, 800)]),
                                           (1, [(800, 800)] * 6), (1, [(50, 50)])])
def test_dpi_stages_generic_invalid(active, stages):
    t = echo()
    with pytest.raises(ValueError):
        RazerMouse(make_info(), t).set_dpi_stages(active, stages)
    assert t.sent == []


# -- poll rate ------------------------------------------------------------------

def test_poll_rate_rejects_rates_outside_database():
    t = echo()
    m = RazerMouse(make_info(), t)
    m.set_poll_rate(500)
    assert packets(t) == [(0x00, 0x05, 0x01, bytes([0x02]))]
    with pytest.raises(ValueError):
        m.set_poll_rate(250)


def test_polling_rate2_read_only():
    # razer_attr_write_poll_rate sends a second set_polling_rate2(.., 0x01) packet whose
    # transaction id the database does not record -> writing is unsupported (never guessed)
    info = make_info(0x1F, extra={"get_poll_rate": cmd(0x1F, "razer_chroma_misc_get_polling_rate2"),
                                  "set_poll_rate": cmd(0x1F, "razer_chroma_misc_set_polling_rate2")})
    t = echo({(0x00, 0xC0): bytes([0, 0x08])})
    m = RazerMouse(info, t)
    assert m.supports("poll_rate") and not m.supports("set_poll_rate")
    assert m.poll_rate() == 1000
    with pytest.raises(UnsupportedFeature):
        m.set_poll_rate(1000)


# -- battery & co. (0x1F device) ------------------------------------------------

def test_battery_device_reads_and_writes():
    t = echo({(0x07, 0x80): bytes([0, 0x80]), (0x07, 0x84): bytes([0, 1]),
              (0x07, 0x83): bytes([0x01, 0x2C]), (0x07, 0x81): bytes([0x3F])})
    m = RazerMouse(make_info(0x1F, battery=True), t)
    assert (m.battery(), m.charging(), m.idle_time(), m.low_battery_threshold()) == (50, True, 300, 25)
    m.set_idle_time(600)
    m.set_low_battery_threshold(15)
    assert packets(t)[-2:] == [(0x07, 0x03, 0x02, bytes([0x02, 0x58])),
                               (0x07, 0x01, 0x01, bytes([0x26]))]
    assert all(r[1] == 0x1F for r in t.raw)


# -- scroll wheel -----------------------------------------------------------------

def test_scroll_options():
    t = echo({(0x02, 0x94): bytes([1, 1]), (0x02, 0x96): bytes([1, 0]), (0x02, 0x97): bytes([1, 1])})
    m = RazerMouse(make_info(), t)
    assert (m.scroll_mode(), m.scroll_acceleration(), m.scroll_smart_reel()) == (1, False, True)
    m.set_scroll_mode(0)
    m.set_scroll_acceleration(True)
    m.set_scroll_smart_reel(False)
    assert packets(t)[3:] == [(0x02, 0x14, 0x02, bytes([VARSTORE, 0])),
                              (0x02, 0x16, 0x02, bytes([VARSTORE, 1])),
                              (0x02, 0x17, 0x02, bytes([VARSTORE, 0]))]
    with pytest.raises(ValueError):
        m.set_scroll_mode(2)
    assert {"set_scroll_mode", "set_scroll_acceleration",
            "set_scroll_smart_reel"} <= set(RazerMouse.PERSISTENT_COMMANDS)


# -- lighting: zones and LED ids (razercommon.h) ----------------------------------

def test_brightness_by_zone_name():
    # logo: extended matrix 0x0F/0x84 (VARSTORE, LOGO_LED 0x04); scroll: standard 0x03/0x83
    t = echo({(0x0F, 0x84): lambda a: bytes([a[0], a[1], 0x40]),
              (0x03, 0x83): lambda a: bytes([a[0], a[1], 0x20])})
    m = RazerMouse(make_info(), t)
    assert m.brightness("logo") == 0x40 and m.brightness("scroll") == 0x20
    m.set_brightness("logo", 200)
    m.set_brightness("scroll", 10, store=VARSTORE)
    assert packets(t) == [(0x0F, 0x84, 3, bytes([VARSTORE, 0x04, 0])),
                          (0x03, 0x83, 3, bytes([VARSTORE, 0x01, 0])),
                          (0x0F, 0x04, 3, bytes([NOSTORE, 0x04, 200])),
                          (0x03, 0x03, 3, bytes([VARSTORE, 0x01, 10]))]
    with pytest.raises(ValueError):
        m.set_brightness("logo", 256)


@pytest.mark.parametrize("zone,led", [("logo", 0x04), ("scroll", 0x01), ("backlight", 0x05),
                                      ("left", 0x11), ("right", 0x10)])
def test_zone_led_ids(zone, led):
    info = make_info(extra={f"set_effect:{zone}:static": cmd(0x1F, EXT + "static")})
    t = echo()
    RazerMouse(info, t).set_effect(zone, Z("static", (9, 8, 7)))
    assert packets(t) == [(0x0F, 0x02, 0x09, bytes([NOSTORE, led, 0x01, 0, 0, 0x01, 9, 8, 7]))]


def test_unknown_zone_is_unsupported():
    info = make_info(extra={"set_effect:thumbgrid:static": cmd(0x1F, EXT + "static")})
    m = RazerMouse(info, echo())
    assert not m.supports("set_effect:thumbgrid:static")


def test_matrix_zone_led_depends_on_builder_family():
    # razer_attr_write_matrix_effect_*: extended -> ZERO_LED, mouse extended -> BACKLIGHT_LED
    info = make_info(extra={"set_effect:matrix:static": cmd(0x1F, EXT + "static"),
                            "set_effect:matrix:spectrum": cmd(0x1F, MEXT + "spectrum"),
                            "get_brightness:matrix": cmd(0x1F, "razer_chroma_extended_matrix_get_brightness")})
    t = echo({(0x0F, 0x84): lambda a: bytes([a[0], a[1], 7])})
    m = RazerMouse(info, t)
    m.set_effect("matrix", Z("static"))
    m.set_effect("matrix", Z("spectrum"))
    assert m.brightness("matrix") == 7
    assert [p[3][1] for p in packets(t)] == [0x00, 0x05, 0x00]


# -- lighting: pattern table --------------------------------------------------------

def test_classic_sequence_static():
    # razer_attr_write_matrix_effect_static_common: led_state(on), led_rgb, led_effect(STATIC)
    t = echo()
    RazerMouse(make_info(), t).set_effect("scroll", Z("static", (1, 2, 3)))
    assert packets(t) == [(0x03, 0x00, 3, bytes([NOSTORE, 0x01, 1])),
                          (0x03, 0x01, 5, bytes([NOSTORE, 0x01, 1, 2, 3])),
                          (0x03, 0x02, 3, bytes([NOSTORE, 0x01, 0x00]))]
    assert all(r[1] == 0x3F for r in t.raw)


@pytest.mark.parametrize("effect,code", [("breath", 0x02), ("blinking", 0x01)])
def test_classic_sequence_breath_and_blinking(effect, code):
    t = echo()
    RazerMouse(make_info(), t).set_effect("scroll", Z(effect, (4, 5, 6)), store=VARSTORE)
    assert [p[3] for p in packets(t)] == [bytes([VARSTORE, 1, 1]), bytes([VARSTORE, 1, 4, 5, 6]),
                                          bytes([VARSTORE, 1, code])]


def test_classic_state_and_effect_spectrum():
    # spectrum_common: led_state(on), led_effect(SPECTRUM 0x04)
    t = echo()
    RazerMouse(make_info(), t).set_effect("scroll", Z("spectrum"))
    assert packets(t) == [(0x03, 0x00, 3, bytes([NOSTORE, 1, 1])),
                          (0x03, 0x02, 3, bytes([NOSTORE, 1, 0x04]))]


def test_classic_state_and_effect_breath_mono_and_on():
    # breath_common (mono): BREATHING 0x02; on_common: STATIC 0x00
    info = make_info(extra={"set_effect:logo:breath": cmd(0x3F, STATE, LEFF),
                            "set_effect:logo:on": cmd(0x3F, STATE, LEFF)})
    t = echo()
    m = RazerMouse(info, t)
    m.set_effect("logo", Z("breath"))
    m.set_effect("logo", Z("on"))
    assert [p[3] for p in packets(t)] == [bytes([0, 4, 1]), bytes([0, 4, 0x02]),
                                          bytes([0, 4, 1]), bytes([0, 4, 0x00])]


def test_led_state_alone_off_and_on():
    # none_common: led_state(false); on_common: led_state(true)
    t = echo()
    m = RazerMouse(make_info(), t)
    m.set_effect("scroll", Z("off"))
    m.set_effect("scroll", Z("on"))
    assert packets(t) == [(0x03, 0x00, 3, bytes([0, 1, 0])), (0x03, 0x00, 3, bytes([0, 1, 1]))]


def test_led_state_or_standard_none_branches_on_led():
    # none_common: `if (led_id == SCROLL_WHEEL_LED) led_state(false) else standard_matrix_effect_none()`
    alt = cmd(0x3F, STATE, "razer_chroma_standard_matrix_effect_none")
    info = make_info(extra={"set_effect:scroll:none": alt, "set_effect:backlight:none": alt})
    t = echo()
    m = RazerMouse(info, t)
    m.set_effect("scroll", Z("off"))
    m.set_effect("backlight", Z("off"))
    assert packets(t) == [(0x03, 0x00, 3, bytes([0, 1, 0])), (0x03, 0x0A, 1, bytes([0x00]))]


@pytest.mark.parametrize("prefix,cls_id,args", [
    (EXT, (0x0F, 0x02, 0x09), bytes([0, 0x04, 0x02, 0x01, 0, 0x01, 7, 8, 9])),
    (MEXT, (0x03, 0x0D, 0x0A), bytes([0, 0x04, 0x03, 0x01, 7, 8, 9, 0, 0, 0])),
    ("razer_chroma_standard_matrix_effect_", (0x03, 0x0A, 0x08), bytes([0x03, 0x01, 7, 8, 9, 0, 0, 0])),
])
def test_breath_alternatives_single_colour(prefix, cls_id, args):
    # breath_common: switch(count) case 3 -> *_breathing_single (one colour)
    info = make_info(extra={"set_effect:logo:breath": breath(prefix, 0x1F)})
    t = echo()
    RazerMouse(info, t).set_effect("logo", Z("breath", (7, 8, 9)))
    assert packets(t) == [(*cls_id, args)]


def test_trinity_matrix_needs_no_led():
    info = make_info(extra={"set_effect:matrix:breath": breath("razer_naga_trinity_effect_", 0x1F),
                            "set_effect:matrix:spectrum": cmd(0x1F, "razer_naga_trinity_effect_spectrum")})
    t = echo()
    m = RazerMouse(info, t)
    m.set_effect("matrix", Z("spectrum"))
    m.set_effect("matrix", Z("breath", (1, 1, 1)))
    assert packets(t)[0] == (0x0F, 0x02, 0x06, bytes([0, 0, 0x03, 0, 0, 0]))
    assert packets(t)[1][:3] == (0x0F, 0x02, 0x09)


def test_unknown_pattern_is_unsupported():
    # same builders in an order no OpenRazer handler uses -> not in the table
    info = make_info(extra={"set_effect:scroll:static": cmd(0x3F, LEFF, RGB, STATE)})
    t = echo()
    m = RazerMouse(info, t)
    assert not m.supports("set_effect:scroll:static")
    with pytest.raises(UnsupportedFeature):
        m.set_effect("scroll", Z("static"))
    assert t.sent == []


def test_pattern_with_effect_it_does_not_cover_is_unsupported():
    # (led_state, led_effect) exists for spectrum/breath/on; "wave" is not one of them
    info = make_info(extra={"set_effect:scroll:wave": cmd(0x3F, STATE, LEFF)})
    assert not RazerMouse(info, echo()).supports("set_effect:scroll:wave")


def test_effect_sends_nothing_on_bad_colour():
    t = echo()
    with pytest.raises(ValueError):
        RazerMouse(make_info(), t).set_effect("scroll", Z("static", (1, 2, 300)))
    assert t.sent == []


def test_every_portable_effect_pattern_in_database_is_classified():
    """No silent gaps: every set_effect steps tuple whose builders all exist is in the table."""
    portable = set()
    for d in load_devices().values():
        for proto in (d.protocol, *d.pid_protocol.values()):
            for k, c in proto.items():
                if k.startswith("set_effect:") and all(
                        s in CMD.BUILDERS and s not in CMD.NOT_PORTABLE for s in c.steps):
                    portable.add(c.steps)
    assert portable <= set(EFFECT_PATTERNS)


def test_every_database_model_constructs_and_answers_supports():
    names = ("firmware", "serial", "dpi", "dpi_stages", "poll_rate", "battery", "charging",
             "idle_time", "low_battery_threshold", "scroll_mode", "scroll_acceleration",
             "scroll_smart_reel")
    for d in load_devices().values():
        for pid in d.pids:
            m = make_mouse(d, FakeTransport(), pid=pid)
            for n in names:
                m.supports(n)
                m.supports("set_" + n)
            for z, effects in m.info.zones.items():
                m.supports(f"brightness:{z}")
                for e in effects:
                    m.supports(f"set_effect:{z}:{e}")


# -- snapshot -----------------------------------------------------------------------

def test_snapshot_only_supported_fields():
    t = echo({(0x00, 0x81): bytes([1, 2]), (0x00, 0x85): bytes([1]),
              (0x04, 0x85): bytes([0, 3, 0x20, 3, 0x20, 0, 0]),
              (0x0F, 0x84): lambda a: bytes([a[0], a[1], 9])})
    snap = RazerMouse(make_info(0x3F, battery=False), t).snapshot()
    assert "battery" not in snap and "idle_time" not in snap
    assert snap["firmware"] == "v1.2" and snap["dpi"] == [800, 800] and snap["poll_rate"] == 1000
    assert snap["brightness"]["logo"] == 9
    assert set(snap["brightness"]) == {"logo", "scroll"}
    assert "scroll_mode" in snap
    assert all(cid & 0x80 for _, cid in t.sent)  # reads only


# -- backup with a generic mouse ------------------------------------------------------

def test_backup_and_restore_generic_mouse_only_supported_fields(tmp_path):
    from razertux.backup import restore_backup, save_backup
    t = echo({(0x00, 0x81): bytes([1, 2]), (0x00, 0x85): bytes([1]),
              (0x04, 0x85): bytes([0, 3, 0x20, 3, 0x20, 0, 0]),
              (0x04, 0x86): bytes([1, 1, 1, 0, 3, 0x20, 3, 0x20, 0, 0]),
              (0x0F, 0x84): lambda a: bytes([a[0], a[1], 9]),
              (0x03, 0x83): lambda a: bytes([a[0], a[1], 5])})
    m = RazerMouse(make_info(0x3F, battery=False), t)
    snap = save_backup(m, tmp_path / "b.json")
    assert "idle_time" not in snap
    t.requests.clear()
    t.sent.clear()
    data = dict(snap, idle_time=300, low_battery_threshold=5)  # e.g. from another model
    assert restore_backup(m, data) == []
    keys = [(r.cmd_class, r.cmd_id) for r in t.requests]
    assert (0x07, 0x03) not in keys and (0x07, 0x01) not in keys
    assert keys[:3] == [(0x00, 0x05), (0x04, 0x06), (0x04, 0x05)]
    assert (0x0F, 0x04) in keys and (0x03, 0x03) in keys
    assert (0x02, 0x14) in keys  # scroll mode written back too


def test_restore_generic_missing_supported_field_is_failed():
    from razertux.backup import restore_backup
    t = echo()
    m = RazerMouse(make_info(0x3F, battery=False), t)
    assert restore_backup(m, {}) == ["poll_rate", "dpi_stages", "dpi", "scroll_mode",
                                     "scroll_acceleration", "scroll_smart_reel"]
    assert t.sent == []
