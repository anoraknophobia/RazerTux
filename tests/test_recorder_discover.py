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
    opened = []
    real_open, real_read_text, real_read_bytes = open, Path.read_text, Path.read_bytes

    def spy_text(self, *a, **k):
        opened.append(str(self))
        return real_read_text(self, *a, **k)

    def spy_bytes(self, *a, **k):
        opened.append(str(self))
        return real_read_bytes(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy_text)
    monkeypatch.setattr(Path, "read_bytes", spy_bytes)
    monkeypatch.setattr("builtins.open", lambda f, *a, **k: (opened.append(str(f)), real_open(f, *a, **k))[1])
    devs = list_razer_devices(str(tmp_path))
    assert opened
    assert not [p for p in opened if p.rsplit("/", 1)[-1] in ("serial", "uniq", "phys")]
    blob = repr(devs)
    assert "FAKE-SERIAL" not in blob and "FAKE-UNIQ" not in blob and "FAKE-PHYS" not in blob


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
    assert "KEY_ESC" in ifs[1]["evdev"]["keyboard"]
    assert "consumer" in ifs[1]["evdev"]
    out = sanitize(Report(device=dev, interfaces=ifs))
    assert out["interfaces"][0]["hid_descriptor_hex"] == DESC.hex()
    assert out["interfaces"][0]["evdev"] == ifs[0]["evdev"]
    assert "FAKE" not in repr(out)


def test_missing_root_is_empty(tmp_path):
    assert list_razer_devices(str(tmp_path / "nope")) == []
