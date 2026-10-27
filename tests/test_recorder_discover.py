import os
from pathlib import Path

from razertux.recorder import discover
from razertux.recorder.discover import list_razer_devices, report_device, report_interfaces
from razertux.recorder.report import Report, sanitize

DESC = bytes.fromhex("05010902a1010901a100c0c0")


def _w(p: Path, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def add_device(root, busport, pid, product, ifaces, vid="1532", bcd="0100", serial=True):
    d = root / "bus/usb/devices" / busport
    for k, v in (("idVendor", vid), ("idProduct", f"{pid:04x}"), ("bcdDevice", bcd),
                 ("manufacturer", "Razer"), ("product", product)):
        _w(d / k, v + "\n")
    if serial:
        _w(d / "serial", "FAKE-SERIAL-123\n")
    n_hidraw = 0
    for num, (cls, sub, proto, inputs) in enumerate(ifaces):
        i = d / f"{busport}:1.{num}"
        for k, v in (("bInterfaceNumber", f"{num:02x}"), ("bInterfaceClass", f"{cls:02x}"),
                     ("bInterfaceSubClass", f"{sub:02x}"), ("bInterfaceProtocol", f"{proto:02x}")):
            _w(i / k, v + "\n")
        if cls != 3:
            continue
        h = i / f"0003:1532:{pid:04X}.000{num}"
        h.mkdir(parents=True)
        (h / "report_descriptor").write_bytes(DESC)
        (h / "hidraw" / f"hidraw{n_hidraw + num}").mkdir(parents=True)
        for m, (name, caps) in enumerate(inputs):
            inp = h / "input" / f"input{num}{m}"
            _w(inp / "name", name + "\n")
            _w(inp / "uniq", "FAKE-UNIQ\n")
            _w(inp / "phys", "usb-FAKE-PHYS/input0\n")
            (inp / f"event{num}{m}").mkdir(parents=True)
            for cap, val in caps.items():
                _w(inp / "capabilities" / cap, val + "\n")


def build(root):
    # Naga V2 Pro (known mouse): iface 0 mouse, iface 1 keyboard + consumer
    add_device(root, "5-1", 0x00A7, "Razer Naga V2 Pro", [
        (3, 1, 2, [("Razer Naga V2 Pro", {"ev": "17", "key": "1f0000 0 0 0 0", "rel": "1943", "msc": "10"})]),
        (3, 1, 1, [("Razer Naga V2 Pro", {"ev": "120013", "key": "0 0 0 0 ffffffffffffffff"}),
                   ("Razer Naga V2 Pro Consumer Control", {"ev": "10001f", "key": "0"})]),
        (3, 0, 0, []),
    ])
    add_device(root, "5-2", 0x0227, "Razer Huntsman", [(3, 1, 1, [("Razer Huntsman", {"ev": "120013", "key": "1"})])])
    add_device(root, "5-3", 0xFFFE, "Razer Mystery", [(3, 0, 0, [])])
    add_device(root, "5-4", 0x1234, "Other Vendor", [(3, 1, 2, [])], vid="046d")
    add_device(root, "1-1", 0x0001, "Hub", [(9, 0, 0, [])], vid="1d6b")


def test_lists_only_razer_grouped(tmp_path):
    build(tmp_path)
    devs = list_razer_devices(str(tmp_path))
    assert [d.pid for d in devs] == [0x00A7, 0x0227, 0xFFFE]
    naga = devs[0]
    assert naga.vid == 0x1532 and naga.bcd_device == "0100"
    assert naga.manufacturer == "Razer" and naga.product == "Razer Naga V2 Pro"
    assert [i.number for i in naga.interfaces] == [0, 1, 2]
    assert [(i.cls, i.subclass, i.protocol) for i in naga.interfaces[:2]] == [(3, 1, 2), (3, 1, 1)]


def test_descriptor_and_nodes(tmp_path):
    build(tmp_path)
    naga = list_razer_devices(str(tmp_path))[0]
    i0, i1, _ = naga.interfaces
    assert i0.descriptor == DESC
    assert [n.path for n in i0.hidraw] == ["/dev/hidraw0"]
    assert [n.path for n in i1.hidraw] == ["/dev/hidraw1"]
    assert [e.path for e in i0.events] == ["/dev/input/event00"]
    assert [e.name for e in i1.events] == ["Razer Naga V2 Pro", "Razer Naga V2 Pro Consumer Control"]
    assert i0.events[0].ev == {0, 1, 2, 4}
    assert i0.events[0].rel == {0, 1, 6, 8, 11, 12}
    assert i0.events[0].key == {64 * 4 + 16 + b for b in range(5)}


def test_bitmap_parsing():
    assert discover.parse_bitmap("1f0000 0 0 0 0") == {64 * 4 + 16 + b for b in range(5)}
    assert discover.parse_bitmap("17") == {0, 1, 2, 4}
    assert discover.parse_bitmap("") == set()
    assert discover.parse_bitmap("zz") == set()


def test_accessible_flags(tmp_path, monkeypatch):
    build(tmp_path)
    ok = {"/dev/hidraw0", "/dev/hidraw1", "/dev/hidraw2", "/dev/input/event00", "/dev/input/event10", "/dev/input/event11"}
    seen = []

    def fake_access(path, mode):
        seen.append((path, mode))
        return path in ok

    monkeypatch.setattr(discover.os, "access", fake_access)
    naga = list_razer_devices(str(tmp_path))[0]
    assert naga.interfaces[0].hidraw[0].readable and naga.interfaces[0].hidraw[0].writable
    assert (("/dev/hidraw0", os.R_OK | os.W_OK)) in seen
    assert (("/dev/input/event00", os.R_OK)) in seen
    assert naga.accessible
    ok.discard("/dev/hidraw1")
    assert not list_razer_devices(str(tmp_path))[0].accessible
    ok.add("/dev/hidraw1")
    ok.discard("/dev/input/event11")
    assert not list_razer_devices(str(tmp_path))[0].accessible


def test_never_reads_serials(tmp_path, monkeypatch):
    build(tmp_path)
    secret = {"serial", "uniq", "phys"}
    for p in tmp_path.rglob("*"):
        if p.name in secret:
            p.chmod(0)  # any read would fail (and is also spied below)
    opened = []
    import builtins
    import io
    real_io_open, real_os_open, real_popen = io.open, os.open, Path.open

    monkeypatch.setattr(builtins, "open", lambda f, *a, **k: (opened.append(str(f)), real_io_open(f, *a, **k))[1])
    monkeypatch.setattr(io, "open", lambda f, *a, **k: (opened.append(str(f)), real_io_open(f, *a, **k))[1])
    monkeypatch.setattr(os, "open", lambda f, *a, **k: (opened.append(str(f)), real_os_open(f, *a, **k))[1])
    monkeypatch.setattr(Path, "open", lambda self, *a, **k: (opened.append(str(self)), real_popen(self, *a, **k))[1])
    devs = list_razer_devices(str(tmp_path))
    assert opened and len(devs) == 3
    assert not [p for p in opened if p.rsplit("/", 1)[-1] in secret]
    blob = repr(devs)
    assert "FAKE-SERIAL" not in blob and "FAKE-UNIQ" not in blob and "FAKE-PHYS" not in blob


def test_unreadable_and_vanishing(tmp_path, monkeypatch):
    build(tmp_path)
    base = tmp_path / "bus/usb/devices/5-1"
    (base / "5-1:1.0/0003:1532:00A7.0000/report_descriptor").chmod(0)
    (base / "product").chmod(0)
    devs = list_razer_devices(str(tmp_path))
    naga = devs[0]
    if os.access(base / "product", os.R_OK):  # running as root: chmod has no effect
        return
    assert naga.product == "" and naga.interfaces[0].descriptor == b""
    # a directory disappearing between listing and reading must not raise
    real_glob = Path.glob

    def vanishing(self, pattern):
        if self.name in ("hidraw", "input"):
            raise FileNotFoundError(self)
        return real_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", vanishing)
    assert [d.pid for d in list_razer_devices(str(tmp_path))] == [0xA7, 0x227, 0xFFFE]


def test_sorted_by_pid_then_busport(tmp_path):
    add_device(tmp_path, "5-9", 0x00A7, "A", [])
    add_device(tmp_path, "5-10", 0x00A7, "B", [])
    add_device(tmp_path, "5-2", 0x0001, "C", [])
    assert [d.product for d in list_razer_devices(str(tmp_path))] == ["C", "B", "A"]


def test_btn_names_ranges_and_keyboard_kind(tmp_path):
    add_device(tmp_path, "5-1", 0x00A7, "Razer Naga V2 Pro", [
        (3, 1, 1, [("Razer Naga V2 Pro Keyboard", {"ev": "120013", "key": "0 0 0 0 fffffffe", "rel": "1943"})]),
        (3, 1, 2, [("Razer Naga V2 Pro", {"ev": "17", "key": "1f0000 0 0 0 0", "rel": "3"})]),
    ])
    ifs = report_interfaces(list_razer_devices(str(tmp_path))[0])
    assert ifs[0]["evdev_keys"] == {"keyboard": "1-31"}
    assert "mouse" not in ifs[0]["evdev"]
    assert "BTN_LEFT" in ifs[1]["evdev"]["mouse"] and "REL_X" in ifs[1]["evdev"]["mouse"]
    assert not any(n.startswith("CODE_") for n in ifs[1]["evdev"]["mouse"])
    out = sanitize(Report(interfaces=ifs))["interfaces"]
    assert out[0]["evdev_keys"] == {"keyboard": "1-31"} and "truncated" not in out[0]


def test_ranges_and_sanitize_strict():
    assert discover._ranges({1, 2, 3, 5, 9, 10}) == "1-3,5,9-10"
    assert discover._ranges(set()) == ""
    big = ",".join(str(i * 2) for i in range(268))
    out = sanitize(Report(interfaces=[{"evdev": {}, "evdev_keys": {"keyboard": big, "bogus": "1", "mouse": "x;y"}}]))
    i = out["interfaces"][0]
    assert i["evdev_keys"] == {"keyboard": big} and i["truncated"] is True
    out = sanitize(Report(interfaces=[{"evdev": {"mouse": [f"K{n}" for n in range(200)]}}]))
    assert out["interfaces"][0]["truncated"] is True and len(out["interfaces"][0]["evdev"]["mouse"]) == 128


def test_report_shapes_known_and_unknown(tmp_path):
    build(tmp_path)
    naga, kb, mystery = list_razer_devices(str(tmp_path))
    dev = report_device(naga)
    assert dev["vid"] == 0x1532 and dev["pid"] == 0x00A7 and dev["kind"] == "mouse"
    assert dev["db_model"] and dev["usb_product"] == "Razer Naga V2 Pro" and dev["bcdDevice"] == "0100"
    assert report_device(kb)["kind"] == "keyboard"
    m = report_device(mystery)
    assert m["kind"] == "unknown" and m["db_model"] == ""
    ifs = report_interfaces(naga)
    assert ifs[0]["number"] == 0 and ifs[0]["class"] == 3 and ifs[0]["protocol"] == 2
    assert ifs[0]["hid_descriptor_hex"] == DESC.hex()
    assert len(ifs[0]["descriptor_sha1"]) == 40
    assert ifs[1]["evdev_keys"]["keyboard"].startswith("0-") or ifs[1]["evdev_keys"]["keyboard"].startswith("1-")
    assert "consumer" in ifs[1]["evdev"]
    out = sanitize(Report(device=dev, interfaces=ifs))
    assert out["interfaces"][0]["hid_descriptor_hex"] == DESC.hex()
    assert out["interfaces"][0]["evdev"] == ifs[0]["evdev"]
    assert "FAKE" not in repr(out)


def test_missing_root_is_empty(tmp_path):
    assert list_razer_devices(str(tmp_path / "nope")) == []


def test_bus_path_names_the_unit_and_is_not_reported(tmp_path):
    add_device(tmp_path, "5-9", 0x00A7, "A", [])
    add_device(tmp_path, "5-10", 0x00A7, "B", [])
    devs = list_razer_devices(str(tmp_path))
    assert [d.bus for d in devs] == ["5-10", "5-9"]
    assert "5-10" not in str(report_device(devs[0])) and "bus" not in report_device(devs[0])
