import pytest

from naga_config.razer import transport as tr
from naga_config.razer.report import (
    STATUS_BUSY,
    STATUS_FAIL,
    STATUS_OK,
    build_report,
)
from naga_config.razer.transport import HidrawTransport, RazerError


def _resp(status, args=b"\x01\x02"):
    raw = bytearray(build_report(0x00, 0x81, args))
    raw[0] = status
    return bytes(raw)


class FakeIo:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.sent = []
        self.gets = 0

    def set_feature(self, fd, buf):
        self.sent.append(bytes(buf))

    def get_feature(self, fd, length):
        self.gets += 1
        return b"\x00" + _resp(self.statuses.pop(0))


def make(statuses):
    t = HidrawTransport("/dev/null")
    io = FakeIo(statuses)
    t._io = io
    return t, io


def test_query_ok():
    t, io = make([STATUS_OK])
    req = build_report(0x00, 0x81, b"", size=2)
    resp = t.query(req)
    assert resp.status == STATUS_OK and resp.args[:2] == b"\x01\x02"
    assert io.sent == [b"\x00" + req]


def test_query_retries_busy():
    t, io = make([STATUS_BUSY, STATUS_BUSY, STATUS_OK])
    assert t.query(build_report(0, 0x81, b"")).status == STATUS_OK
    assert io.gets == 3


def test_query_busy_exhausted():
    t, io = make([STATUS_BUSY] * 11)
    with pytest.raises(RazerError):
        t.query(build_report(0, 0x81, b""))


def test_query_fail_raises():
    t, _ = make([STATUS_FAIL])
    with pytest.raises(RazerError):
        t.query(build_report(0, 0x81, b""))


def test_find_control_hidraw_none(monkeypatch):
    class Ctx:
        def list_devices(self, **kw):
            return []

    monkeypatch.setattr(tr.pyudev, "Context", Ctx)
    assert tr.find_control_hidraw() is None
