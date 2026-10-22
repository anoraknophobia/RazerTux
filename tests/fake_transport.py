"""Fake Transport for tests: canned responses keyed by (class, id), logs requests."""
from naga_config.razer.report import (
    STATUS_OK,
    STATUS_UNSUPPORTED,
    RazerResponse,
    parse_report,
)
from naga_config.razer.transport import RazerError


# set-command ids (class, id) echoed back by FakeTransport.full()
WRITE_COMMANDS = [(0x04, 0x05), (0x04, 0x06), (0x00, 0x05), (0x0F, 0x02),
                  (0x0F, 0x04), (0x07, 0x03), (0x07, 0x01)]


class FakeTransport:
    def __init__(self, responses=None, default=None):
        """responses: {(cls, id): bytes | callable(request_args) -> bytes}.
        default: a STATUS_* code returned (as RazerError) for unknown commands."""
        self.responses = dict(responses or {})
        self.default = default
        self.sent = []  # [(cmd_class, cmd_id)]
        self.requests = []  # parsed request RazerResponse objects

    def query(self, report: bytes) -> RazerResponse:
        req = parse_report(report)
        key = (req.cmd_class, req.cmd_id)
        self.sent.append(key)
        self.requests.append(req)
        if key in self.responses:
            val = self.responses[key]
            args = val(req.args) if callable(val) else val
            return RazerResponse(STATUS_OK, req.cmd_class, req.cmd_id, len(args), args)
        status = self.default if self.default is not None else STATUS_UNSUPPORTED
        raise RazerError(f"command {key[0]:#04x}/{key[1]:#04x} status {status:#04x}")

    @classmethod
    def full(cls):
        stage = lambda i, d: bytes([i, d >> 8, d & 0xFF, d >> 8, d & 0xFF, 0, 0])
        return cls(
            {
                (0x00, 0x81): bytes([1, 2]),
                (0x00, 0x82): b"PM2345678901234567890\x00",
                (0x04, 0x85): bytes([0x00, 0x03, 0x20, 0x03, 0x20, 0, 0]),
                (0x04, 0x86): bytes([1, 2, 3])
                + stage(1, 800) + stage(2, 1600) + stage(3, 3200),
                (0x00, 0x85): bytes([0x01]),
                (0x0F, 0x84): lambda a: bytes([a[0], a[1], 0x80]),
                (0x07, 0x80): bytes([0, 0xFF]),
                (0x07, 0x84): bytes([0, 1]),
                (0x07, 0x83): bytes([0x01, 0x2C]),
                (0x07, 0x81): bytes([0x3F]),
                (0x02, 0x98): bytes([1, 3]),
                **{k: (lambda a: a) for k in WRITE_COMMANDS},
            }
        )
