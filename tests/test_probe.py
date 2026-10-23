"""Read probe: gates writes on experimental mice. Hardware findings 2026-10-23."""
from fake_transport import WRITE_COMMANDS, FakeTransport
from test_razer_mouse import make_info

from razertux import __version__
from razertux.backup import backup_path
from razertux.razer.mouse import RazerMouse
from razertux.razer.probe import diagnostics, probe
from razertux.razer.report import STATUS_OK, RazerResponse
from razertux.razer.transport import RazerError

INFO = make_info(txn=0x1F)


def dpi_bytes(d):
    return bytes([0, d >> 8, d & 0xFF, d >> 8, d & 0xFF, 0, 0])


def good(over=None):
    t = FakeTransport.full()
    t.responses.update(over or {})
    return t


def test_probe_ok_with_plausible_answers():
    t = good()
    r = probe(RazerMouse(INFO, t))
    assert r.ok, r.problems
    assert r.values["dpi"] == (800, 800)
    assert set(r.values) == {"firmware", "dpi", "poll_rate"}
    assert r.raw


def test_probe_sends_no_write_command():
    t = good()
    probe(RazerMouse(INFO, t))
    assert t.sent
    assert not set(t.sent) & set(WRITE_COMMANDS)


def test_probe_restores_transport():
    t = good()
    m = RazerMouse(INFO, t)
    probe(m)
    assert m._t is t


def test_probe_fails_on_unsupported_status():
    r = probe(RazerMouse(INFO, FakeTransport()))
    assert not r.ok and r.problems


def test_probe_fails_on_dpi_out_of_range():
    r = probe(RazerMouse(INFO, good({(0x04, 0x85): dpi_bytes(52582)})))
    assert not r.ok
    assert any("dpi" in p for p in r.problems)


def test_probe_fails_on_mismatched_class_id_echo():
    class Stale(FakeTransport):
        def query(self, report):
            resp = super().query(report)
            if (report[6], report[7]) == (0x04, 0x85):
                return RazerResponse(STATUS_OK, 0x00, 0x81, resp.size, resp.args)
            return resp

    r = probe(RazerMouse(INFO, Stale(good().responses)))
    assert not r.ok
    assert any("echo" in p for p in r.problems)


def test_probe_fails_on_poll_rate_not_listed():
    r = probe(RazerMouse(INFO, good({(0x00, 0x85): bytes([0x7F])})))
    assert not r.ok


def test_probe_fails_on_zero_firmware():
    r = probe(RazerMouse(INFO, good({(0x00, 0x81): bytes([0, 0])})))
    assert not r.ok


def test_diagnostics_contains_pid_and_hex_but_no_serial():
    t = good()
    r = probe(RazerMouse(INFO, t))
    text = diagnostics(INFO, 0x00AB, r, r.raw)
    assert "0x00ab" in text and __version__ in text and INFO.model in text
    assert r.raw[0][1].hex(" ") in text
    assert "PM2345678901234567890" not in text
    assert "50 4d 32 33" not in text  # hex of the serial


def test_backup_path_per_model(tmp_path):
    assert backup_path("naga_v2_pro", tmp_path) == tmp_path / "backups" / "naga_v2_pro.json"


def test_probe_records_response_of_real_style_non_ok_error():
    """HidrawTransport raises RazerError (carrying the response) on non-OK status."""
    class Raising(FakeTransport):
        def query(self, report):
            if (report[6], report[7]) == (0x04, 0x85):
                resp = RazerResponse(0x05, 0x04, 0x85, 0, b"")
                raise RazerError("command 0x04/0x85 failed with status 0x05", response=resp)
            return super().query(report)

    r = probe(RazerMouse(INFO, Raising(good().responses)))
    assert not r.ok
    assert any("dpi" in p for p in r.problems)
    assert any(b[:4] == bytes([0x05, 0x04, 0x85, 0]) for _, b in r.raw)
    assert "05 04 85 00" in diagnostics(INFO, 0x00AB, r, r.raw)

