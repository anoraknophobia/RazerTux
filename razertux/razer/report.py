"""Razer 90-byte feature report: build and parse.

Layout: status(0) txn(1) remaining_packets BE16(2-3) protocol_type(4)
data_size(5) command_class(6) command_id(7) args[80](8-87) crc(88) reserved(89)
"""
from dataclasses import dataclass

REPORT_LEN = 90
ARGS_LEN = 80

STATUS_OK = 0x02
STATUS_BUSY = 0x01
STATUS_FAIL = 0x03
STATUS_TIMEOUT = 0x04
STATUS_UNSUPPORTED = 0x05


@dataclass(frozen=True)
class RazerResponse:
    status: int
    cmd_class: int
    cmd_id: int
    size: int
    args: bytes


def build_report(
    cmd_class: int,
    cmd_id: int,
    args: bytes,
    size: int | None = None,
    txn: int = 0x1F,
) -> bytes:
    if len(args) > ARGS_LEN:
        raise ValueError(f"args too long: {len(args)} > {ARGS_LEN}")
    if size is None:
        size = len(args)
    buf = bytearray(REPORT_LEN)
    buf[1] = txn
    buf[5] = size
    buf[6] = cmd_class
    buf[7] = cmd_id
    buf[8 : 8 + len(args)] = args
    crc = 0
    for b in buf[2:88]:
        crc ^= b
    buf[88] = crc
    return bytes(buf)


def parse_report(data: bytes) -> RazerResponse:
    if len(data) != REPORT_LEN:
        raise ValueError(f"report must be {REPORT_LEN} bytes, got {len(data)}")
    size = data[5]
    return RazerResponse(
        status=data[0],
        cmd_class=data[6],
        cmd_id=data[7],
        size=size,
        args=bytes(data[8 : 8 + min(size, ARGS_LEN)]),
    )
