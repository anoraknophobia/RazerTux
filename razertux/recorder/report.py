"""Device report: format, privacy filter, human summary and prefilled GitHub issue URL."""
from __future__ import annotations

import contextvars
import getpass
import json
import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

SCHEMA = "razertux-device-report"
VERSION = 1
NOTES_MAX = 2000
URL_MAX = 8000

READ_NAMES = {"firmware", "serial_present", "dpi", "dpi_stages", "poll_rate", "battery", "charging", "brightness",
              "device_mode", "idle_time", "low_battery_threshold", "scroll_mode", "scroll_acceleration",
              "scroll_smart_reel"}
_EVDEV_NODES = {"mouse", "keyboard", "consumer", "system", "other", "misc"}
_DEVICE_KEYS = {"vid", "pid", "usb_manufacturer", "usb_product", "bcdDevice", "kind", "db_model", "connection"}
# the user's answer to "What kind of device is this?" for devices the database does not know (R-4)
USER_KINDS = frozenset({"mouse", "keyboard", "keypad", "headset", "other"})
_SYSTEM_KEYS = {"razertux", "kernel", "distro"}
_BUTTON_KEYS = {"template", "results", "signals", "standard_keys_seen", "duplicates", "timed_out", "stopped_at"}
_TOP = ("schema", "version", "device", "system", "interfaces", "buttons", "reads", "light", "notes")
_HEX = re.compile(r"^[0-9a-fA-F]*$")
_NAME = re.compile(r"^[A-Za-z0-9_]{1,40}$")
_ZONE = re.compile(r"^[a-z0-9_]{1,24}$")
_RANGES = re.compile(r"^[0-9]{1,4}(?:-[0-9]{1,4})?(?:,[0-9]{1,4}(?:-[0-9]{1,4})?)*$")
KEYS_MAX = 4096
_BAD_KEY = re.compile(r"(?i)serial|uniq|phys|mac|host|user")
RAW_MAX = 256
DESCRIPTOR_MAX = 16384
MAX_ITEMS = 128
MAX_IFACES = 16

_MAC = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{2}(?:[:-][0-9a-f]{2}){5}(?![0-9a-f])")
_HEX12 = re.compile(r"(?i)(?<![0-9a-z])[0-9a-f]{12}(?![0-9a-z])")
_PATH = re.compile(r"/(?:home|sys|dev|root|run|proc|mnt|media|tmp)(?:/[^\s,;\"'\)\]]*)?")


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


_LOCAL = contextvars.ContextVar("razertux_report_local_identity", default=True)


def _secret_words() -> set[str]:
    words: set[str] = set()
    if not _LOCAL.get():
        return words
    for fn in (_uname_node, _user):
        w = fn()
        if len(w) >= 3:
            words.add(w)
    return words


def _scrub(s, limit: int | None = None) -> str:
    s = str(s)
    s = _PATH.sub("<path>", s)
    s = _MAC.sub("<addr>", s)
    s = _HEX12.sub("<addr>", s)
    for w in _secret_words():
        s = re.sub(r"(?i)(?<![0-9A-Za-z_])" + re.escape(w) + r"(?![0-9A-Za-z_])", "<redacted>", s)
    return s[:limit] if limit else s


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


def _d(v) -> dict:
    return v if isinstance(v, dict) else {}


def _l(v) -> list:
    return list(v) if isinstance(v, (list, tuple)) else []


def _scalar(v, limit: int = 64):
    """int/float/bool/short scrubbed string; anything else is dropped (returns the sentinel)."""
    if isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, str) and len(v) <= limit:
        return _scrub(v, limit)
    return _DROP


_DROP = object()


_VALUE_STR = re.compile(r"^(?:[vV]?\d+(?:\.\d+){0,3}|[a-z_]{1,24})$")


def _value(v):
    if isinstance(v, str):
        return v if _VALUE_STR.match(v) else _DROP
    if isinstance(v, (list, tuple)):
        items = [_scalar(x) for x in v[:16]]
        return [x for x in items if x is not _DROP] if len(v) <= 16 else _DROP
    return _scalar(v)


def _strlist(v, limit: int = 64) -> list[str]:
    return [_scrub(x, limit) for x in _l(v)[:MAX_ITEMS] if isinstance(x, str)]


def _names(v) -> list[str]:
    return [x for x in _l(v)[:MAX_ITEMS] if isinstance(x, str) and _NAME.match(x)]


def _hex(v, cap: int, exact: int | None = None):
    if not isinstance(v, str) or not _HEX.match(v):
        return None
    if exact is not None and len(v) != exact:
        return None
    return v if len(v) <= cap else None


def _read(name, entry):
    base = str(name).split(":", 1)[0]
    zone = str(name).split(":", 1)[1] if ":" in str(name) else None
    if base not in READ_NAMES or (zone is not None and (base != "brightness" or not _ZONE.match(zone))):
        return None
    if _BAD_KEY.search(str(name)) and name != "serial_present":
        return None
    e = _d(entry)
    out: dict = {}
    if "raw" in e:
        raw = _hex(e["raw"], RAW_MAX)
        if raw is not None:
            out["raw"] = raw
        elif isinstance(e["raw"], str) and _HEX.match(e["raw"]):
            out["truncated"] = True
    if "value" in e:
        if name == "serial_present":
            out["value"] = bool(e["value"])
        else:
            val = _value(e["value"])
            if val is not _DROP:
                out["value"] = val
    if isinstance(e.get("status"), str):
        out["status"] = _scrub(e["status"], 32)
    return out


def _interface(i: dict) -> dict:
    out: dict = {}
    for k in ("number", "class", "subclass", "protocol"):
        if isinstance(i.get(k), int) and not isinstance(i.get(k), bool):
            out[k] = i[k]
    if "hid_descriptor_hex" in i:
        h = _hex(i["hid_descriptor_hex"], DESCRIPTOR_MAX)
        if h is not None:
            out["hid_descriptor_hex"] = h
        elif isinstance(i["hid_descriptor_hex"], str) and _HEX.match(i["hid_descriptor_hex"]):
            out["truncated"] = True
    sha = _hex(i.get("descriptor_sha1"), 40, exact=40)
    if sha is not None:
        out["descriptor_sha1"] = sha
    ev = _d(i.get("evdev"))
    out["evdev"] = {}
    for k in sorted(ev):
        if k in _EVDEV_NODES:
            out["evdev"][k] = _names(ev[k])
            if len(_l(ev[k])) > MAX_ITEMS:
                out["truncated"] = True
    keys = {}
    for k, v in sorted(_d(i.get("evdev_keys")).items()):
        if k not in _EVDEV_NODES:
            continue
        if isinstance(v, str) and len(v) <= KEYS_MAX and _RANGES.match(v):
            keys[k] = v
        else:
            out["truncated"] = True
    if keys:
        out["evdev_keys"] = keys
    return out


def sanitize(data, local_identity: bool = True):
    """Whitelist report sections/keys and scrub every string value; accepts a Report or its dict form.

    ``local_identity=False`` skips the machine-specific scrub of this host's user and host name
    (for checking a report that was produced elsewhere, e.g. by the maintainer import tool)."""
    token = _LOCAL.set(bool(local_identity))
    try:
        return _sanitize(data)
    finally:
        _LOCAL.reset(token)


def _sanitize(data):
    if isinstance(data, Report):
        data = {k: getattr(data, k) for k in _TOP}
    data = _d(data)
    dev = _d(data.get("device"))
    device = {}
    for k in _DEVICE_KEYS:
        if k in dev:
            v = _scalar(dev[k], 64)
            if v is not _DROP:
                device[k] = v
    if isinstance(dev.get("user_kind"), str) and dev["user_kind"] in USER_KINDS:
        device["user_kind"] = dev["user_kind"]
    sysd = {}
    for k in _SYSTEM_KEYS:
        v = _scalar(_d(data.get("system")).get(k, _DROP), 64) if k in _d(data.get("system")) else _DROP
        if v is not _DROP:
            sysd[k] = v
    b = _d(data.get("buttons"))
    buttons: dict = {}
    if "template" in b and _scalar(b["template"], 40) is not _DROP:
        buttons["template"] = _scalar(b["template"], 40)
    results = {}
    for k, v in _d(b.get("results")).items():
        if _NAME.match(str(k)) and not _BAD_KEY.search(str(k)):
            results[str(k)] = "absent" if v == "absent" else _strlist(v)
    buttons["results"] = results
    buttons["signals"] = _strlist(b.get("signals"))
    sk = b.get("standard_keys_seen")
    if isinstance(sk, (list, tuple)):  # old shape: a list of labels
        sk = len(sk)
    buttons["standard_keys_seen"] = sk if isinstance(sk, int) and not isinstance(sk, bool) and sk >= 0 else 0
    buttons["duplicates"] = _strlist(b.get("duplicates"))
    if b.get("timed_out") is True:
        buttons["timed_out"] = True
    if isinstance(b.get("stopped_at"), str) and _NAME.match(b["stopped_at"]):
        buttons["stopped_at"] = b["stopped_at"]
    reads = {}
    for k, v in _d(data.get("reads")).items():
        r = _read(k, v)
        if r is not None:
            reads[str(k)] = r
    light = data.get("light")
    if isinstance(light, str):
        light = _scrub(light, 80)
    else:
        light = {str(k): v for k, v in _d(light).items()
                 if _ZONE.match(str(k)) and not _BAD_KEY.search(str(k)) and v in ("yes", "no", "unsure")}
    ifaces = [_interface(i) for i in _l(data.get("interfaces"))[:MAX_IFACES] if isinstance(i, dict)]
    out = {"schema": SCHEMA, "version": VERSION, "device": device, "system": sysd, "interfaces": ifaces,
           "buttons": buttons, "reads": reads, "light": light, "notes": _scrub(data.get("notes") or "")[:NOTES_MAX]}
    return {k: out[k] for k in _TOP}


def system_section(os_release: str = "/etc/os-release", release=platform.release) -> dict:
    """The ``system`` section: RazerTux version, kernel release, distribution NAME + VERSION_ID."""
    from razertux import __version__
    fields: dict[str, str] = {}
    try:
        for line in Path(os_release).read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = line.partition("=")
            if sep and k in ("NAME", "VERSION_ID"):
                fields[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    distro = " ".join(fields[k] for k in ("NAME", "VERSION_ID") if fields.get(k)) or "unknown"
    return {"razertux": __version__, "kernel": str(release())[:64], "distro": distro[:64]}


def to_json(report: Report) -> str:
    return json.dumps(sanitize(report), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def summary_text(report: Report) -> str:
    d = sanitize(report)
    dev, sysd, b = d["device"], d["system"], d["buttons"]
    lines = [
        f"Device: {dev.get('usb_product', '?')} ({dev.get('usb_manufacturer', '?')})",
        f"USB ID: {_hex4(dev.get('vid'))}:{_hex4(dev.get('pid'))}  bcdDevice {dev.get('bcdDevice', '?')}",
        f"Kind: {dev.get('kind', '?')}"
        + (f" (user says: {dev['user_kind']})" if dev.get("user_kind") else "")
        + f"  Connection: {dev.get('connection', '?')}",
        f"Database model: {dev.get('db_model') or 'not in database'}",
        f"RazerTux {sysd.get('razertux', '?')}, kernel {sysd.get('kernel', '?')}, {sysd.get('distro', '?')}",
        f"Interfaces: {len(d['interfaces'])}",
    ]
    for i in d["interfaces"]:
        ev = "; ".join(f"{k}: {','.join(v)}" for k, v in i["evdev"].items() if v) or "none"
        lines.append(f"  #{i.get('number', '?')} class {i.get('class', '?')}/{i.get('subclass', '?')}/"
                     f"{i.get('protocol', '?')} sha1 {i.get('descriptor_sha1', '?')[:8]} evdev {ev}")
    if b.get("template"):
        lines.append(f"Button template: {b['template']}")
    for name, sig in sorted(b["results"].items()):
        lines.append(f"  {name}: {sig if isinstance(sig, str) else ', '.join(sig) or 'no signal'}")
    if b["signals"]:
        lines.append("Other signals: " + ", ".join(b["signals"]))
    if b["duplicates"]:
        lines.append("Ambiguous (same signal on several buttons): " + ", ".join(b["duplicates"]))
    if b.get("timed_out"):
        lines.append(f"Recording timed out at {b.get('stopped_at', '?')}")
    if b["standard_keys_seen"]:
        lines.append(f"Standard keys seen on {b['standard_keys_seen']} input source(s)")
    for k, v in sorted(d["reads"].items()):
        lines.append(f"Read {k}: value {v.get('value', '-')} status {v.get('status', '?')}")
    light = d["light"]
    if light:
        lines.append("Light: " + (light if isinstance(light, str) else ", ".join(f"{k}={v}" for k, v in sorted(light.items()))))
    return "\n".join(lines)


def _hex4(v) -> str:
    return f"{v:04x}" if isinstance(v, int) and not isinstance(v, bool) else str(v or "?")[:8]


def issue_url(report: Report, repo: str = "anoraknophobia/RazerTux") -> tuple[str, bool]:
    """Prefilled new-issue URL; second value is False when the JSON had to be left out to stay <= 8000 chars."""
    d = sanitize(report)
    dev = d["device"]
    name = f"{str(dev.get('usb_product', 'unknown'))[:56]} {_hex4(dev.get('vid'))}:{_hex4(dev.get('pid'))}"[:80]
    base = f"https://github.com/{repo}/issues/new"

    def build(summary: str, notes: str, js: str | None) -> str:
        q = {"template": "device-report.yml", "title": f"[device] {name}", "device": name,
             "summary": summary, "notes": notes}
        if js is not None:
            q["report"] = js
        return base + "?" + "&".join(f"{k}={quote(v, safe=',:' if k == 'report' else '')}" for k, v in q.items())

    summary, notes = summary_text(report), d["notes"]
    full = build(summary, notes, to_json(report))
    if len(full) <= URL_MAX:
        return full, True
    url = build(summary, notes, None)
    while len(url) > URL_MAX and (notes or summary):
        cut = (len(url) - URL_MAX) // 3 + 1  # encoded chars expand at most 3x per char... bytes up to 9; loop continues
        if notes:
            notes = notes[:-cut]
        else:
            summary = summary[:-cut]
        url = build(summary, notes, None)
    return url, False
