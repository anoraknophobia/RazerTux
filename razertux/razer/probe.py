"""Read-only probe of a mouse; gates writes on experimental (unverified) devices.

Only read commands are sent. Hardware findings of 2026-10-23: an unknown command can
return a stale response buffer echoing a previous command's class/id, and DPI 52582 was
seen while the stage state was corrupt - so the echo is checked and values range-checked.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import __version__
from ..devices.db import DeviceInfo
from .mouse import RazerMouse
from .report import STATUS_OK, RazerResponse
from .transport import RazerError

DPI_MIN = 100


@dataclass
class ProbeResult:
    ok: bool
    values: dict[str, Any] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)
    raw: list[tuple[str, bytes]] = field(default_factory=list)


class _Recorder:
    """Transport wrapper recording (label, request class/id, response) per query."""

    def __init__(self, inner):
        self._inner = inner
        self.label = ""
        self.records: list[tuple[str, int, int, RazerResponse | None]] = []

    def query(self, report: bytes) -> RazerResponse:
        try:
            resp = self._inner.query(report)
        except RazerError as e:  # real transport raises on non-OK status: keep the evidence
            self.records.append((self.label, report[6], report[7], getattr(e, "response", None)))
            raise
        self.records.append((self.label, report[6], report[7], resp))
        return resp


def _raw(resp: RazerResponse | None) -> bytes:
    if resp is None:
        return b""
    return bytes([resp.status, resp.cmd_class, resp.cmd_id, resp.size]) + resp.args


def probe(mouse: RazerMouse) -> ProbeResult:
    info = mouse.info
    rec = _Recorder(mouse._t)
    mouse._t = rec
    values: dict[str, Any] = {}
    problems: list[str] = []
    checked = 0

    def check_range(name: str, v: Any) -> str | None:
        if name == "dpi":
            x, y = v
            if not all(DPI_MIN <= d <= info.dpi_max for d in (x, y)):
                return f"dpi {x}/{y} outside {DPI_MIN}..{info.dpi_max}"
        elif name == "poll_rate":
            if v not in info.poll_rates:
                return f"poll_rate {v} not in {list(info.poll_rates)}"
        elif name == "firmware":
            if v in ("v0.0", "0.0"):
                return "firmware bytes both 0"
        return None

    try:
        for name in ("firmware", "dpi", "poll_rate"):
            if not mouse.supports(name):
                continue
            checked += 1
            rec.label = name
            start = len(rec.records)
            try:
                v = getattr(mouse, name)()
            except Exception as e:  # RazerError, ValueError, parse errors
                problems.append(f"{name}: read failed ({e})")
                continue
            for _, cls, cid, resp in rec.records[start:]:
                if resp is None:
                    problems.append(f"{name}: no response for {cls:#04x}/{cid:#04x}")
                    continue
                if resp.status != STATUS_OK:
                    problems.append(f"{name}: status {resp.status:#04x}")
                if (resp.cmd_class, resp.cmd_id) != (cls, cid):
                    problems.append(
                        f"{name}: response echoes {resp.cmd_class:#04x}/{resp.cmd_id:#04x}, "
                        f"expected {cls:#04x}/{cid:#04x}")
            values[name] = v
            msg = check_range(name, v)
            if msg:
                problems.append(msg)
                bad = True
    finally:
        mouse._t = rec._inner

    if checked == 0:
        problems.append("no readable core feature (firmware, dpi, poll_rate)")
    raw = [(f"{label} {cls:#04x}/{cid:#04x}" + (" (no response)" if resp is None else ""),
            _raw(resp)) for label, cls, cid, resp in rec.records]
    return ProbeResult(ok=not problems, values=values, problems=problems, raw=raw)


def diagnostics(info: DeviceInfo, pid: int, result: ProbeResult,
                raw: list[tuple[str, bytes]]) -> str:
    """Plain text for GitHub issues. No serial number - only whether one exists."""
    lines = [
        f"Model: {info.model} ({info.name})",
        f"PID: {pid:#06x}",
        f"RazerTux: {__version__}",
        f"Serial: {'present' if 'serial' in info.features else 'not present'}",
        f"Result: {'OK' if result.ok else 'FAILED'}",
    ]
    lines += [f"Value {k}: {v}" for k, v in result.values.items()]
    lines += [f"Problem: {p}" for p in result.problems]
    lines.append("Raw responses (status class id size args):")
    lines += [f"  {label}: {data.hex(' ')}" for label, data in raw]
    return "\n".join(lines) + "\n"
