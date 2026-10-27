import pytest
from fake_transport import FakeTransport

from razertux.razer.naga_v2_pro import LOGO, SCROLL, THUMB, ZONE_LED, NagaV2Pro
from razertux.razer.report import STATUS_UNSUPPORTED


def test_dpi_parses_big_endian():
    t = FakeTransport({(0x04, 0x85): bytes([0x00, 0x03, 0x20, 0x03, 0x20, 0, 0])})
    assert NagaV2Pro(t).dpi() == (800, 800)
    assert t.requests[0].args[0] == 0x00  # NOSTORE
    assert t.requests[0].size == 7


def test_dpi_distinct_xy():
    t = FakeTransport({(0x04, 0x85): bytes([0, 0x06, 0x40, 0x03, 0x20, 0, 0])})
    assert NagaV2Pro(t).dpi() == (1600, 800)


def test_dpi_stages():
    dev = NagaV2Pro(FakeTransport.full())
    assert dev.dpi_stages() == (2, [(800, 800), (1600, 1600), (3200, 3200)])


def test_dpi_stages_request_layout():
    t = FakeTransport.full()
    NagaV2Pro(t).dpi_stages()
    assert t.requests[0].args[0] == 0x01 and t.requests[0].size == 0x26


@pytest.mark.parametrize("raw,hz", [(0x01, 1000), (0x02, 500), (0x08, 125)])
def test_poll_rate(raw, hz):
    t = FakeTransport({(0x00, 0x85): bytes([raw])})
    assert NagaV2Pro(t).poll_rate() == hz


def test_poll_rate_unknown_value():
    t = FakeTransport({(0x00, 0x85): bytes([0x55])})
    with pytest.raises(ValueError):
        NagaV2Pro(t).poll_rate()


def test_firmware_and_serial():
    dev = NagaV2Pro(FakeTransport.full())
    assert dev.firmware() == "v1.2"
    assert dev.serial() == "PM2345678901234567890"


def test_brightness_per_led():
    t = FakeTransport.full()
    dev = NagaV2Pro(t)
    assert dev.brightness(LOGO) == 0x80
    assert t.requests[0].args[:2] == bytes([0x01, LOGO])
    assert (LOGO, SCROLL, THUMB) == (0x04, 0x01, 0x05)
    assert ZONE_LED == {"logo": LOGO, "scroll": SCROLL, "thumb": THUMB}


def test_battery_scaled_to_percent():
    t = FakeTransport({(0x07, 0x80): bytes([0, 0xFF])})
    assert NagaV2Pro(t).battery() == 100
    t = FakeTransport({(0x07, 0x80): bytes([0, 0x80])})
    assert NagaV2Pro(t).battery() == 50


def test_charging_idle_threshold():
    dev = NagaV2Pro(FakeTransport.full())
    assert dev.charging() is True
    assert dev.idle_time() == 300
    assert dev.low_battery_threshold() == 25


def test_snapshot_never_sends_class_0x02():
    t = FakeTransport.full()
    snap = NagaV2Pro(t).snapshot()
    assert "scroll_profile" not in snap
    assert all(c != 0x02 for c, _ in t.sent)
    assert not hasattr(NagaV2Pro, "scroll_profile")


def test_snapshot_survives_unsupported_field():
    t = FakeTransport(default=STATUS_UNSUPPORTED)
    snap = NagaV2Pro(t).snapshot()
    assert snap["dpi"] is None and snap["poll_rate"] is None


def test_snapshot_oserror_per_field():
    t = FakeTransport.full()
    def boom(_):
        raise OSError("io")
    t.responses[(0x07, 0x80)] = boom
    snap = NagaV2Pro(t).snapshot()
    assert snap["battery"] is None
    assert snap["dpi"] == [800, 800]


def test_snapshot_full_contents():
    snap = NagaV2Pro(FakeTransport.full()).snapshot()
    assert snap["dpi_stages"] == {"active": 2, "stages": [[800, 800], [1600, 1600], [3200, 3200]]}
    assert snap["brightness"] == {"logo": 0x80, "scroll": 0x80, "thumb": 0x80}
    assert snap["poll_rate"] == 1000
    assert snap["low_battery_threshold"] == 25


def test_no_write_commands_sent_by_snapshot():
    t = FakeTransport.full()
    NagaV2Pro(t).snapshot()
    assert t.sent
    assert all(cid & 0x80 for (_, cid) in t.sent)  # only get-IDs (bit 7)


# -- write commands (layouts from OpenRazer razerchromacommon.c) --------------

from razertux.config.model import LightingZone
from razertux.razer.naga_v2_pro import (
    NOSTORE,
    PERSISTENT_COMMANDS,
    REACTIVE_SPEED,
    VARSTORE,
    WAVE_DIRECTION,
)


def sent(fn, *a, **kw):
    t = FakeTransport.full()
    fn(NagaV2Pro(t), *a, **kw)
    return t.requests


def one(fn, *a, **kw):
    reqs = sent(fn, *a, **kw)
    assert len(reqs) == 1
    return reqs[0]


def test_set_dpi_layout():
    r = one(lambda d: d.set_dpi(1800, 900))
    assert (r.cmd_class, r.cmd_id, r.size) == (0x04, 0x05, 0x07)
    assert r.args[:7] == bytes([NOSTORE, 0x07, 0x08, 0x03, 0x84, 0, 0])


def test_set_dpi_varstore_and_range():
    r = one(lambda d: d.set_dpi(800, 800, store=VARSTORE))
    assert r.args[0] == VARSTORE
    for bad in (99, 30001):
        with pytest.raises(ValueError):
            NagaV2Pro(FakeTransport.full()).set_dpi(bad, 800)


def test_set_dpi_stages_layout():
    r = one(lambda d: d.set_dpi_stages(2, [(800, 800), (1600, 3200)]))
    assert (r.cmd_class, r.cmd_id, r.size) == (0x04, 0x06, 0x26)
    assert r.args[:3] == bytes([NOSTORE, 2, 2])  # store, active (1-based), count
    assert r.args[3:10] == bytes([1, 0x03, 0x20, 0x03, 0x20, 0, 0])  # 1-based ids
    assert r.args[10:17] == bytes([2, 0x06, 0x40, 0x0C, 0x80, 0, 0])
    assert r.args[17:] == bytes(len(r.args) - 17)


def test_set_dpi_stages_five_ok_six_rejected():
    one(lambda d: d.set_dpi_stages(5, [(800, 800)] * 5))
    t = FakeTransport.full()
    with pytest.raises(ValueError):
        NagaV2Pro(t).set_dpi_stages(1, [(800, 800)] * 6)
    assert t.sent == []


@pytest.mark.parametrize("active,stages", [
    (1, []), (0, [(800, 800)]), (2, [(800, 800)]),
    (1, [(50, 50)]), (1, [(30050, 800)]), (1, [(810, 800)]),
])
def test_set_dpi_stages_invalid(active, stages):
    t = FakeTransport.full()
    with pytest.raises(ValueError):
        NagaV2Pro(t).set_dpi_stages(active, stages)
    assert t.sent == []


def test_set_dpi_stages_varstore():
    r = one(lambda d: d.set_dpi_stages(1, [(2500, 2500)], store=VARSTORE))
    assert r.args[0] == VARSTORE


@pytest.mark.parametrize("hz,raw", [(1000, 0x01), (500, 0x02), (125, 0x08)])
def test_set_poll_rate_layout(hz, raw):
    r = one(lambda d: d.set_poll_rate(hz))
    assert (r.cmd_class, r.cmd_id, r.size) == (0x00, 0x05, 0x01)
    assert r.args[0] == raw


def test_set_poll_rate_invalid():
    with pytest.raises(ValueError):
        NagaV2Pro(FakeTransport.full()).set_poll_rate(250)


def test_set_brightness_layout():
    r = one(lambda d: d.set_brightness(LOGO, 191))
    assert (r.cmd_class, r.cmd_id, r.size) == (0x0F, 0x04, 0x03)
    assert r.args[:3] == bytes([NOSTORE, LOGO, 191])
    assert one(lambda d: d.set_brightness(THUMB, 0, store=VARSTORE)).args[:3] == bytes(
        [VARSTORE, THUMB, 0])
    for bad in (-1, 256):
        with pytest.raises(ValueError):
            NagaV2Pro(FakeTransport.full()).set_brightness(LOGO, bad)


def Z(effect, color=(1, 2, 3)):
    return LightingZone(effect, color, 100)


def test_effect_off():
    r = one(lambda d: d.set_effect(LOGO, Z("off")))
    assert (r.cmd_class, r.cmd_id, r.size) == (0x0F, 0x02, 0x06)
    assert r.args[:6] == bytes([NOSTORE, LOGO, 0x00, 0, 0, 0])


def test_effect_static():
    r = one(lambda d: d.set_effect(THUMB, Z("static", (255, 0, 16))))
    assert (r.cmd_class, r.cmd_id, r.size) == (0x0F, 0x02, 0x09)
    assert r.args[:9] == bytes([NOSTORE, THUMB, 0x01, 0, 0, 0x01, 255, 0, 16])


def test_effect_breath_single_colour():
    r = one(lambda d: d.set_effect(LOGO, Z("breath", (9, 8, 7))))
    assert r.size == 0x09
    assert r.args[:9] == bytes([NOSTORE, LOGO, 0x02, 0x01, 0x00, 0x01, 9, 8, 7])


def test_effect_spectrum_has_no_colour():
    r = one(lambda d: d.set_effect(LOGO, Z("spectrum")))
    assert r.size == 0x06
    assert r.args[:6] == bytes([NOSTORE, LOGO, 0x03, 0, 0, 0])


def test_effect_wave_has_no_colour_and_default_direction():
    assert WAVE_DIRECTION == 1
    r = one(lambda d: d.set_effect(LOGO, Z("wave")))
    assert r.size == 0x06
    assert r.args[:6] == bytes([NOSTORE, LOGO, 0x04, WAVE_DIRECTION, 0x28, 0])


def test_effect_reactive_default_speed_and_colour():
    assert REACTIVE_SPEED == 2
    r = one(lambda d: d.set_effect(LOGO, Z("reactive", (4, 5, 6))))
    assert r.size == 0x09
    assert r.args[:9] == bytes([NOSTORE, LOGO, 0x05, 0, REACTIVE_SPEED, 0x01, 4, 5, 6])


def test_effect_unknown_rejected_before_sending():
    t = FakeTransport.full()
    with pytest.raises(ValueError):
        NagaV2Pro(t).set_effect(LOGO, Z("fireworks"))
    assert t.sent == []


def test_set_idle_time_layout_and_range():
    r = one(lambda d: d.set_idle_time(300))
    assert (r.cmd_class, r.cmd_id, r.size) == (0x07, 0x03, 0x02)
    assert r.args[:2] == bytes([0x01, 0x2C])
    for bad in (59, 901):
        with pytest.raises(ValueError):
            NagaV2Pro(FakeTransport.full()).set_idle_time(bad)


@pytest.mark.parametrize("pct,raw", [(5, 0x0C), (15, 0x26), (25, 0x3F)])
def test_set_low_battery_threshold_layout(pct, raw):
    r = one(lambda d: d.set_low_battery_threshold(pct))
    assert (r.cmd_class, r.cmd_id, r.size) == (0x07, 0x01, 0x01)
    assert r.args[0] == raw


def test_low_battery_threshold_roundtrip_and_range():
    for pct in range(5, 26):
        t = FakeTransport.full()
        d = NagaV2Pro(t)
        d.set_low_battery_threshold(pct)
        t.responses[(0x07, 0x81)] = bytes([t.requests[0].args[0]])
        assert d.low_battery_threshold() == pct
    for bad in (4, 26):
        with pytest.raises(ValueError):
            NagaV2Pro(FakeTransport.full()).set_low_battery_threshold(bad)


def test_every_flagged_set_command_uses_nostore_at_arg0():
    d_t = FakeTransport.full()
    d = NagaV2Pro(d_t)
    d.set_dpi(800, 800)
    d.set_dpi_stages(1, [(800, 800)])
    d.set_brightness(LOGO, 1)
    for eff in ("off", "static", "breath", "spectrum", "wave", "reactive"):
        d.set_effect(LOGO, Z(eff))
    assert len(d_t.requests) == 9
    assert all(r.args[0] == NOSTORE for r in d_t.requests)


def test_commands_without_storage_flag_are_marked_persistent():
    assert set(PERSISTENT_COMMANDS) == {
        "set_poll_rate", "set_idle_time", "set_low_battery_threshold",
        "set_dpi", "set_dpi_stages"}  # NOSTORE not honoured for DPI
    assert all(PERSISTENT_COMMANDS.values())
    for name in PERSISTENT_COMMANDS:
        assert hasattr(NagaV2Pro, name)


def test_write_does_not_touch_device_mode():
    t = FakeTransport.full()
    d = NagaV2Pro(t)
    d.set_poll_rate(500)
    d.set_dpi(800, 800)
    assert (0x00, 0x04) not in t.sent


def _onboard(active, dpis):
    entries = b"".join(bytes([i, d >> 8, d & 0xFF, d >> 8, d & 0xFF, 0, 0])
                       for i, d in enumerate(dpis, start=1))
    return bytes([VARSTORE, active, len(dpis)]) + entries


def test_repair_active_stage_valid_table_untouched():
    t = FakeTransport({(0x04, 0x83): _onboard(2, [2500, 800, 1600])})
    assert NagaV2Pro(t).repair_active_stage() == (800, 800)
    assert t.sent == [(0x04, 0x83)]
    assert t.requests[0].args[0] == VARSTORE and t.requests[0].size == 0x26


def test_repair_active_stage_invalid_id_reactivates_first():
    # seen on hardware: active id 0x0b with 5 stages -> firmware used DPI 52582
    table = _onboard(0x0B, [2500, 800, 1600, 3200, 6400])
    t = FakeTransport({(0x04, 0x83): table, (0x04, 0x06): lambda a: a})
    assert NagaV2Pro(t).repair_active_stage() == (2500, 2500)
    w = t.requests[1]
    assert (w.cmd_class, w.cmd_id, w.size) == (0x04, 0x06, 0x26)
    assert w.args[:3] == bytes([VARSTORE, 1, 5])
    assert w.args[3:3 + 35] == table[3:]  # stage entries written back unchanged


@pytest.mark.parametrize("table", [
    bytes([VARSTORE, 9, 0]),                       # no stages
    _onboard(9, [2500, 800])[:10],                 # truncated
    _onboard(9, [2500])[:3] + bytes([1, 0xCD, 0x66, 0xCD, 0x66, 0, 0]),  # 52582
])
def test_repair_active_stage_invalid_table_not_written(table):
    t = FakeTransport({(0x04, 0x83): table, (0x04, 0x06): lambda a: a})
    assert NagaV2Pro(t).repair_active_stage() is None
    assert (0x04, 0x06) not in t.sent


def test_dpi_stages_reads_onboard_table_with_1_based_ids():
    t = FakeTransport({(0x04, 0x83): _onboard(3, [800, 1600, 2500, 3200, 6400])})
    assert NagaV2Pro(t).dpi_stages() == (3, [(800, 800), (1600, 1600), (2500, 2500),
                                             (3200, 3200), (6400, 6400)])


def test_dpi_stages_unknown_active_id_reported_as_first():
    t = FakeTransport({(0x04, 0x83): _onboard(0x0B, [2500, 800])})
    assert NagaV2Pro(t).dpi_stages() == (1, [(2500, 2500), (800, 800)])


def test_scroll_stage_read_and_set_follow_synapse():
    # Synapse capture 2026-10-27: get 0x15/0x80 [01 00] -> [01 slot]; set 0x15/0x00 [01 slot]
    t = FakeTransport({(0x15, 0x80): bytes([0x01, 0x04]), (0x15, 0x00): bytes([0x01, 0x02])})
    naga = NagaV2Pro(t)
    assert naga.scroll_stage() == 4
    naga.set_scroll_stage(2)
    get, put = t.requests
    assert (get.cmd_class, get.cmd_id, bytes(get.args[:2])) == (0x15, 0x80, bytes([1, 0]))
    assert (put.cmd_class, put.cmd_id, bytes(put.args[:2])) == (0x15, 0x00, bytes([1, 2]))
    assert get.size == 2 and put.size == 2


@pytest.mark.parametrize("slot", [0, 7, -1, "2", True])
def test_scroll_stage_rejects_bad_slots(slot):
    t = FakeTransport()
    with pytest.raises(ValueError):
        NagaV2Pro(t).set_scroll_stage(slot)
    assert t.sent == []
