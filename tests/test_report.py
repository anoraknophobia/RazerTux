import functools
import operator

import pytest

from razertux.razer.report import (
    STATUS_OK,
    build_report,
    parse_report,
)


def test_build_report_battery_query():
    r = build_report(0x07, 0x80, b"\x00\x00")
    assert len(r) == 90
    assert r[1] == 0x1F and r[5] == 0x02 and r[6] == 0x07 and r[7] == 0x80
    assert r[88] == functools.reduce(operator.xor, r[2:88])


def test_parse_report_roundtrip():
    raw = bytearray(build_report(0x04, 0x85, b"\x00" * 7))
    raw[0] = 0x02
    resp = parse_report(bytes(raw))
    assert (resp.status, resp.cmd_class, resp.cmd_id, resp.size) == (0x02, 0x04, 0x85, 7)
    assert resp.args == b"\x00" * 7
    assert resp.status == STATUS_OK


def test_build_report_explicit_size_and_args_too_long():
    r = build_report(0x00, 0x81, b"", size=2)
    assert r[5] == 2
    with pytest.raises(ValueError):
        build_report(0x00, 0x81, b"\x00" * 81)


def test_parse_report_wrong_length():
    with pytest.raises(ValueError):
        parse_report(b"\x00" * 10)
