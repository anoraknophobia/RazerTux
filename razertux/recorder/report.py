"""Device report: format, privacy filter, human summary and prefilled GitHub issue URL."""
from __future__ import annotations

import getpass
import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlencode

SCHEMA = "razertux-device-report"
VERSION = 1
NOTES_MAX = 2000
URL_MAX = 8000

# Whitelist of keys per section; None = free-form mapping (keys and values are scrubbed, never dropped).
_ALLOWED: dict[str, set[str] | None] = {
    "device": {"vid", "pid", "usb_manufacturer", "usb_product", "bcdDevice", "kind", "db_model", "connection"},
    "system": {"razertux", "kernel", "distro"},
    "buttons": {"template", "results", "signals", "standard_keys_seen"},
    "reads": None,
    "light": None,
}
_IFACE_KEYS = {"number", "class", "subclass", "protocol", "hid_descriptor_hex", "descriptor_sha1", "evdev"}
_HEX_KEYS = {"hid_descriptor_hex", "descriptor_sha1", "raw"}
_TOP = ("schema", "version", "device", "system", "interfaces", "buttons", "reads", "light", "notes")

_MAC = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}(?![0-9a-f])")
_HEX12 = re.compile(r"(?i)(?<![0-9a-z])[0-9a-f]{12}(?![0-9a-z])")
_PATH = re.compile(r"/(?:home|sys|dev|root|run|proc)(?:/[^\s,;\"'\)\]]*)?")


@dataclass
class Report:
    device: dict = field(default_factory=dict)
    system: dict = field(default_factory=dict)
    interfaces: list = field(default_factory=list)
    buttons: dict = field(default_factory=dict)
    reads: dict = field(default_factory=dict)
    light: dict | str = field(default_factory=dict)
    notes: str = ""
    schema: str = SCHEMA
    version: int = VERSION


def _scrub(s: str) -> str:
    s = _PATH.sub("<path>", s)
    s = _MAC.sub("<addr>", s)
    s = _HEX12.sub("<addr>", s)
    for secret in {os.environ.get("HOSTNAME", ""), _uname_node(), _user()}:
        if len(secret) >= 3:
            s = re.sub(re.escape(secret), "<redacted>", s, flags=re.I)
    return s


def _uname_node() -> str:
    try:
        return os.uname().nodename
    except Exception:
        return ""


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return ""


def _clean(v, hex_ok: bool = False):
    if isinstance(v, str):
        return v if hex_ok else _scrub(v)
    if isinstance(v, dict):
        return {_scrub(str(k)): _clean(x, str(k) in _HEX_KEYS) for k, x in v.items()
                if str(k).lower() not in {"serial", "uniq", "phys", "hostname", "user", "username", "mac"}}
    if isinstance(v, (list, tuple)):
        return [_clean(x, hex_ok) for x in v]
    if isinstance(v, (bool, int, float)) or v is None:
        return v
    return _scrub(str(v))


def sanitize(data):
    """Whitelist report sections/keys and scrub every string value; accepts a Report or its dict form."""
    if isinstance(data, Report):
        data = {k: getattr(data, k) for k in _TOP}
    out: dict = {"schema": SCHEMA, "version": VERSION}
    for sec, allowed in _ALLOWED.items():
        v = data.get(sec)
        if isinstance(v, str):
            out[sec] = _scrub(v)
        elif isinstance(v, dict):
            out[sec] = _clean({k: x for k, x in v.items() if allowed is None or k in allowed})
        else:
            out[sec] = {}
    out["interfaces"] = [_clean({k: x for k, x in i.items() if k in _IFACE_KEYS})
                         for i in data.get("interfaces") or [] if isinstance(i, dict)]
    out["notes"] = _scrub(str(data.get("notes") or ""))[:NOTES_MAX]
    return {k: out[k] for k in _TOP}


def to_json(report: Report) -> str:
    return json.dumps(sanitize(report), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def summary_text(report: Report) -> str:
    d = sanitize(report)
    dev, sysd, b = d["device"], d["system"], d["buttons"]
    lines = [
        f"Device: {dev.get('usb_product', '?')} ({dev.get('usb_manufacturer', '?')})",
        f"USB ID: {_hex4(dev.get('vid'))}:{_hex4(dev.get('pid'))}  bcdDevice {dev.get('bcdDevice', '?')}",
        f"Kind: {dev.get('kind', '?')}  Connection: {dev.get('connection', '?')}",
        f"Database model: {dev.get('db_model') or 'not in database'}",
        f"RazerTux {sysd.get('razertux', '?')}, kernel {sysd.get('kernel', '?')}, {sysd.get('distro', '?')}",
        f"Interfaces: {len(d['interfaces'])}",
    ]
    if b:
        lines.append(f"Button template: {b.get('template', '?')}")
        for name, sig in sorted((b.get("results") or {}).items()):
            lines.append(f"  {name}: {sig if isinstance(sig, str) else ', '.join(map(str, sig)) or 'no signal'}")
        if b.get("signals"):
            lines.append("Other signals: " + ", ".join(map(str, b["signals"])))
    if d["reads"]:
        lines.append("Reads: " + ", ".join(f"{k}={v.get('status', '?')}" if isinstance(v, dict) else k
                                          for k, v in sorted(d["reads"].items())))
    if d["light"]:
        light = d["light"]
        lines.append("Light: " + (light if isinstance(light, str) else ", ".join(f"{k}={v}" for k, v in sorted(light.items()))))
    return "\n".join(lines)


def _hex4(v) -> str:
    return f"{v:04x}" if isinstance(v, int) else str(v or "?")


def issue_url(report: Report, repo: str = "anoraknophobia/RazerTux") -> tuple[str, bool]:
    """Prefilled new-issue URL; second value is False when the JSON had to be left out to stay <= 8000 chars."""
    d = sanitize(report)
    dev = d["device"]
    name = f"{dev.get('usb_product', 'unknown')} {_hex4(dev.get('vid'))}:{_hex4(dev.get('pid'))}"
    base = f"https://github.com/{repo}/issues/new"

    def build(summary: str, notes: str, js: str | None) -> str:
        q = {"template": "device-report.yml", "title": f"[device] {name}", "device": name,
             "summary": summary, "notes": notes}
        if js is not None:
            q["report"] = js
        return f"{base}?{urlencode(q, quote_via=quote)}"

    summary, notes = summary_text(report), d["notes"]
    full = build(summary, notes, to_json(report))
    if len(full) <= URL_MAX:
        return full, True
    url = build(summary, notes, None)
    while len(url) > URL_MAX and (summary or notes):
        if len(notes) > 200:
            notes = notes[:len(notes) // 2]
        else:
            summary = summary[:len(summary) * 3 // 4]
        url = build(summary, notes, None)
    return url, False
