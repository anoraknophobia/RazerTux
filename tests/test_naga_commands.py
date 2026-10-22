import pytest
from fake_transport import FakeTransport

from naga_config.razer.naga_v2_pro import LOGO, SCROLL, THUMB, ZONE_LED, NagaV2Pro
from naga_config.razer.report import STATUS_UNSUPPORTED


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


def test_scroll_profile_value_and_readonly_request():
    t = FakeTransport.full()
    assert NagaV2Pro(t).scroll_profile() == 3
    assert t.requests[0].args[0] == 0x01 and t.requests[0].size == 0x02


def test_scroll_profile_unsupported_is_none():
    t = FakeTransport(default=STATUS_UNSUPPORTED)
    assert NagaV2Pro(t).scroll_profile() is None


def test_snapshot_survives_unsupported_field():
    t = FakeTransport(default=STATUS_UNSUPPORTED)
    snap = NagaV2Pro(t).snapshot()
    assert snap["scroll_profile"] is None
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
