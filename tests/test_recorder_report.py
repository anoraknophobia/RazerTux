import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from razertux.recorder.report import Report, issue_url, sanitize, summary_text, to_json

YML = Path(__file__).resolve().parent.parent / ".github/ISSUE_TEMPLATE/device-report.yml"


def make(**kw):
    r = Report(
        device={"vid": 0x1532, "pid": 0x0067, "usb_manufacturer": "Razer", "usb_product": "Razer Naga Trinity",
                "bcdDevice": "0200", "kind": "mouse", "db_model": "naga_trinity", "connection": "wired"},
        system={"razertux": "0.3.0", "kernel": "7.2.10-1-cachyos", "distro": "CachyOS 20260101"},
        interfaces=[{"number": 0, "class": 3, "subclass": 1, "protocol": 2, "hid_descriptor_hex": "05010902",
                    "descriptor_sha1": "a" * 40, "evdev": {"mouse": ["BTN_LEFT"]}}],
        buttons={"template": "naga_12", "results": {"BTN_1": ["KEY_1"], "BTN_2": "absent"},
                 "signals": ["KEY_F13"], "standard_keys_seen": ["BTN_LEFT"]},
        reads={"dpi": {"raw": "0102", "value": 800, "status": "ok"}},
        light={"logo": "yes"},
        notes="works",
    )
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def test_schema_header():
    d = json.loads(to_json(make()))
    assert d["schema"] == "razertux-device-report" and d["version"] == 1


def test_to_json_compact_sorted():
    s = to_json(make())
    assert ": " not in s and ", " not in s
    assert list(json.loads(s)) == sorted(json.loads(s))


def test_sanitize_drops_unknown_and_sensitive_keys():
    r = make()
    r.device["serial"] = "XX1234"
    r.device["hostname"] = "box"
    r.interfaces[0]["evdev"]["uniq"] = "zz"
    r.interfaces[0]["phys"] = "usb-0000:00:14.0-2/input0"
    out = json.dumps(sanitize(json.loads(json.dumps(r.__dict__))))
    for bad in ("XX1234", "box", "uniq", "phys", "usb-0000"):
        assert bad not in out


def test_sanitize_scrubs_values():
    r = make(notes="mac aa:bb:cc:dd:ee:ff or AABBCCDDEEFF at /home/bob/x, /sys/devices/y /dev/hidraw3 ok")
    s = to_json(r)
    assert not re.search(r"(?i)aa:bb:cc|aabbccddeeff|/home/|/sys/|/dev/|bob", s)
    assert "ok" in s


def test_hex_fields_not_mangled():
    r = make()
    r.reads["x"] = {"raw": "aabbccddeeff", "value": 1, "status": "ok"}
    assert "aabbccddeeff" in to_json(r)


def test_kernel_and_distro_kept():
    s = to_json(make())
    assert "7.2.10-1-cachyos" in s and "CachyOS 20260101" in s


def test_notes_truncated():
    assert len(json.loads(to_json(make(notes="a" * 5000)))["notes"]) == 2000


def test_summary_content():
    t = summary_text(make())
    for needle in ("1532", "0067", "Naga Trinity", "naga_trinity", "wired", "0.3.0", "naga_12", "BTN_2", "absent"):
        assert needle in t


def test_url_fields_match_yaml():
    ids = set(re.findall(r"^\s+id: (\w+)$", YML.read_text(), re.M))
    url, full = issue_url(make())
    q = parse_qs(urlparse(url).query)
    assert full
    assert set(q) - {"template", "title"} <= ids
    assert {"device", "summary", "report", "notes"} == ids
    assert q["template"] == ["device-report.yml"] and q["title"][0].startswith("[device] ")
    assert urlparse(url).path == "/anoraknophobia/RazerTux/issues/new"


def test_yaml_form_attributes():
    t = YML.read_text()
    assert "device-report" in t and 'title: "[device] "' in t and "render: json" in t


def test_url_too_long_drops_json_keeps_summary():
    r = make(reads={f"r{i}": {"raw": "ab" * 40, "value": i, "status": "ok"} for i in range(200)})
    url, full = issue_url(r)
    assert not full and len(url) <= 8000
    q = parse_qs(urlparse(url).query)
    assert "report" not in q and q["summary"][0]


def test_url_length_bound_always():
    url, _ = issue_url(make(notes="é" * 2000))
    assert len(url) <= 8000
