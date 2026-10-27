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
                 "signals": ["KEY_F13"], "standard_keys_seen": 1},
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
    r.reads["dpi"] = {"raw": "aabbccddeeff", "value": 1, "status": "ok"}
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
    r = make(interfaces=[{"number": i, "class": 3, "hid_descriptor_hex": "ab" * 2000} for i in range(3)])
    url, full = issue_url(r)
    assert not full and len(url) <= 8000
    q = parse_qs(urlparse(url).query)
    assert not q.get("report", [""])[0].lstrip().startswith("{") and q["summary"][0]


def test_url_length_bound_always():
    url, _ = issue_url(make(notes="é" * 2000))
    assert len(url) <= 8000


def _leaks(s):
    return re.search(r"(?i)/home/|00:11:22|PM2012H|bob\b|/mnt|/tmp|/media|/run/user", s)


def test_read_name_whitelist_and_key_variants():
    bad = {"raw": "00", "value": "PM2012H12345678 /home/bob 00:11:22:33:44:55", "status": "ok"}
    r = make(reads={"get_serial_number": bad, "SerialNumber": bad, "dpi_mac": bad, "unknown": bad,
                    "dpi": bad, "serial_present": {"value": 1, "status": "ok"}, "brightness:logo": {"value": 5}})
    out = sanitize(r)["reads"]
    assert set(out) == {"dpi", "serial_present", "brightness:logo"}
    assert out["serial_present"]["value"] is True
    assert "value" not in out["dpi"]


def test_hex_validation_and_caps():
    r = make(interfaces=[{"number": 0, "hid_descriptor_hex": "zz/home/bob", "descriptor_sha1": "ab" * 5,
                          "evdev": {"mouse": ["BTN_LEFT", "/home/bob", "x y"], "evil": ["KEY_A"], "uniq": ["a"]}}],
             reads={"dpi": {"raw": "/home/bob", "value": 1}, "poll_rate": {"raw": "ab" * 300, "value": 2}})
    s = sanitize(r)
    i = s["interfaces"][0]
    assert "hid_descriptor_hex" not in i and "descriptor_sha1" not in i
    assert i["evdev"] == {"mouse": ["BTN_LEFT"]}
    assert "raw" not in s["reads"]["dpi"]
    assert "raw" not in s["reads"]["poll_rate"] and s["reads"]["poll_rate"]["truncated"] is True
    assert not _leaks(json.dumps(s))


def test_value_types():
    r = make(reads={"dpi": {"value": {"a": 1}}, "poll_rate": {"value": "x" * 100}, "battery": {"value": 55.5}})
    out = sanitize(r)["reads"]
    assert "value" not in out["dpi"] and "value" not in out["poll_rate"] and out["battery"]["value"] == 55.5


def test_non_dict_sections_no_crash():
    r = make(device="x", system=5, buttons=[1], light=None, reads=[], interfaces=["a", None, 3])
    r.buttons = {"results": "oops", "signals": 4}
    sanitize(r)
    to_json(r)
    summary_text(r)
    issue_url(r)
    sanitize(make(buttons=None, device=None, light=[]))
    sanitize("nonsense")


def test_user_scrub_whole_word(monkeypatch):
    monkeypatch.setenv("LOGNAME", "alice")
    s = to_json(make(notes="alice said malice at /mnt/a /tmp/b /media/c /run/user/1000/x"))
    assert "alice said" not in s and "malice" in s and not _leaks(s)


def test_summary_rich():
    t = summary_text(make())
    for needle in ("#0", "class 3/1/2", "aaaaaaaa", "BTN_LEFT", "Standard keys seen", "Read dpi", "800", "logo=yes"):
        assert needle in t


def test_yaml_ids_unique_lowercase():
    ids = re.findall(r"^\s+id: (\w+)$", YML.read_text(), re.M)
    assert len(ids) == len(set(ids)) and all(i == i.lower() for i in ids)


def test_long_product_and_notes_terminate():
    r = make(notes="é" * 2000)
    r.device["usb_product"] = "P" * 5000
    r.interfaces = [{"number": i, "hid_descriptor_hex": "ab" * 2000} for i in range(10)]
    url, full = issue_url(r)
    assert len(url) <= 8000 and not full
    r2 = make(notes="n")
    r2.device["usb_product"] = "é" * 5000
    url2, _ = issue_url(r2)
    assert len(url2) <= 8000


def test_standard_keys_seen_int_contract():
    def sk(v):
        return sanitize(make(buttons={"results": {}, "standard_keys_seen": v}))["buttons"]["standard_keys_seen"]
    assert sk(3) == 3 and sk(0) == 0
    assert sk(True) == 0 and sk(-1) == 0 and sk("x") == 0 and sk(None) == 0
    assert sk(["a", "b"]) == 2  # old list shape
    assert "Standard keys seen on 2 input source(s)" in summary_text(make(buttons={"results": {}, "standard_keys_seen": 2}))
    d = sanitize(make())
    assert sanitize(d) == d


def test_buttons_timeout_and_duplicates_roundtrip():
    b = {"template": "mmo", "results": {"side_1": ["a:b:KEY_1"]}, "signals": [], "standard_keys_seen": 0,
         "duplicates": ["a:b:KEY_1"], "timed_out": True, "stopped_at": "side_2"}
    out = sanitize(make(buttons=b))["buttons"]
    assert out == b
    assert "timed out at side_2" in summary_text(make(buttons=b))


def test_system_section(tmp_path):
    from razertux.recorder.report import system_section
    osr = tmp_path / "os-release"
    osr.write_text('NAME="Arch Linux"\nID=arch\nVERSION_ID=2026.10\nPRETTY_NAME="x"\n')
    s = system_section(str(osr), release=lambda: "7.2.10-1-cachyos")
    assert s == {"razertux": __import__("razertux").__version__, "kernel": "7.2.10-1-cachyos",
                 "distro": "Arch Linux 2026.10"}
    assert system_section(str(tmp_path / "missing"), release=lambda: "6.1")["distro"] == "unknown"
    assert sanitize({"system": s})["system"] == s


def test_user_kind_whitelisted_and_in_summary():
    r = make()
    r.device["kind"] = "unknown"
    r.device["user_kind"] = "keyboard"
    d = sanitize(r)
    assert d["device"]["kind"] == "unknown" and d["device"]["user_kind"] == "keyboard"
    assert "user says: keyboard" in summary_text(r)
    for bad in ("toaster", "unknown", 3, "keyboard and more"):
        r.device["user_kind"] = bad
        assert "user_kind" not in sanitize(r)["device"]



def test_sanitize_sorts_every_signal_list():
    """Ruling R-6: whatever order a (hand-made) report has, the sanitized one stores none."""
    data = {"buttons": {"template": "free", "signals": ["if1:keyboard:KEY_3", "if1:keyboard:KEY_1"],
                        "results": {"side_1": ["if1:keyboard:KEY_9", "if1:keyboard:KEY_2"], "side_2": "absent"},
                        "duplicates": ["if1:keyboard:KEY_9", "if1:keyboard:KEY_2"]}}
    b = sanitize(data)["buttons"]
    assert b["signals"] == ["if1:keyboard:KEY_1", "if1:keyboard:KEY_3"]
    assert b["results"] == {"side_1": ["if1:keyboard:KEY_2", "if1:keyboard:KEY_9"], "side_2": "absent"}
    assert b["duplicates"] == ["if1:keyboard:KEY_2", "if1:keyboard:KEY_9"]


def test_dpi_stages_value_survives_sanitize():
    r = make(reads={"dpi_stages": {"status": "ok", "value": {"active": 2, "stages": [[800, 800], [1600, 1600]]}}})
    v = sanitize(r)["reads"]["dpi_stages"]["value"]
    assert v == {"active": 2, "stages": [[800, 800], [1600, 1600]]}
    assert "active 2" in summary_text(r) and "800x800" in summary_text(r)


def test_dpi_stages_bad_shapes_are_dropped():
    for bad in ({"active": "x", "stages": []}, {"active": 1, "stages": [["/home/bob", 1]]},
                {"active": 1, "stages": [[1, 1]] * 17}, {"other": 1}):
        r = make(reads={"dpi_stages": {"status": "ok", "value": bad}})
        assert "value" not in sanitize(r)["reads"]["dpi_stages"]


def test_url_too_long_puts_attach_hint_in_report_field():
    r = make(interfaces=[{"number": i, "class": 3, "hid_descriptor_hex": "ab" * 2000} for i in range(3)])
    url, full = issue_url(r, file_name="razertux-report-naga_trinity.json")
    q = parse_qs(urlparse(url).query)
    assert not full and len(url) <= 8000
    assert "razertux-report-naga_trinity.json" in q["report"][0]
    assert not q["report"][0].lstrip().startswith("{")
