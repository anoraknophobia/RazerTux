import json

from fake_transport import FakeTransport

from razertux import cli
from razertux.backup import load_backup, save_backup
from razertux.razer.naga_v2_pro import NagaV2Pro


def test_backup_never_overwrites(tmp_path):
    p = tmp_path / "mouse-backup.json"
    p.write_text('{"keep": 1}')
    save_backup(NagaV2Pro(FakeTransport.full()), p)
    assert json.loads(p.read_text()) == {"keep": 1}


def test_backup_does_not_touch_device_when_exists(tmp_path):
    p = tmp_path / "b.json"
    p.write_text("{}")
    t = FakeTransport.full()
    save_backup(NagaV2Pro(t), p)
    assert t.sent == []


def test_backup_roundtrip_creates_dirs(tmp_path):
    p = tmp_path / "a" / "b" / "mouse-backup.json"
    snap = save_backup(NagaV2Pro(FakeTransport.full()), p)
    assert load_backup(p) == snap
    assert snap["dpi"] == [800, 800]


class _Ctx:
    def __init__(self, t):
        self.t = t
    def __enter__(self):
        return self.t
    def __exit__(self, *a):
        pass


def test_cli_status_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_open_transport", lambda path: _Ctx(FakeTransport.full()))
    assert cli.main(["status", "--hidraw", "/dev/null"]) == 0
    assert json.loads(capsys.readouterr().out)["poll_rate"] == 1000


def test_cli_backup_writes_then_refuses(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_open_transport", lambda path: _Ctx(FakeTransport.full()))
    p = tmp_path / "bk.json"
    assert cli.main(["backup", "--path", str(p)]) == 0
    first = p.read_text()
    assert cli.main(["backup", "--path", str(p)]) == 1
    assert "existiert bereits" in capsys.readouterr().err
    assert p.read_text() == first


def test_cli_default_backup_path(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.default_backup_path() == tmp_path / ".config/razertux/backups/naga_v2_pro.json"


def test_backup_refuses_when_core_fields_missing(tmp_path):
    import pytest
    from razertux.backup import BackupError
    p = tmp_path / "b.json"
    with pytest.raises(BackupError):
        save_backup(NagaV2Pro(FakeTransport(default=5)), p)
    assert not p.exists()


def test_cli_backup_dead_device_exit1_no_file(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_open_transport", lambda path: _Ctx(FakeTransport(default=5)))
    p = tmp_path / "bk.json"
    assert cli.main(["backup", "--path", str(p)]) == 1
    assert "nichts gesichert" in capsys.readouterr().err
    assert not p.exists()


def test_cli_backup_warns_about_none_fields(monkeypatch, tmp_path, capsys):
    t = FakeTransport.full()
    del t.responses[(0x07, 0x83)]
    monkeypatch.setattr(cli, "_open_transport", lambda path: _Ctx(t))
    assert cli.main(["backup", "--path", str(tmp_path / "bk.json")]) == 0
    err = capsys.readouterr().err
    assert "idle_time" in err and "scroll_profile" not in err


# -- restore ------------------------------------------------------------------

REAL = {
    "firmware": "v1.2", "serial": "X", "dpi": [1800, 1800],
    "dpi_stages": {"active": 1, "stages": [[2500, 2500]]}, "poll_rate": 500,
    "brightness": {"logo": 191, "scroll": None, "thumb": 191}, "battery": 80,
    "charging": False, "idle_time": 300, "low_battery_threshold": 5, "scroll_profile": 0,
}


def test_restore_writes_everything_with_varstore_and_skips_mode():
    from razertux.backup import restore_backup
    t = FakeTransport.full()
    assert restore_backup(NagaV2Pro(t), REAL) == []
    by = {(r.cmd_class, r.cmd_id): r for r in t.requests}
    assert by[(0x00, 0x05)].args[0] == 0x02
    assert by[(0x04, 0x06)].args[:3] == bytes([0x01, 1, 1])  # VARSTORE, active 1, count 1
    assert by[(0x04, 0x05)].args[:5] == bytes([0x01, 0x07, 0x08, 0x07, 0x08])
    bright = [r.args[:3] for r in t.requests if (r.cmd_class, r.cmd_id) == (0x0F, 0x04)]
    assert bright == [bytes([1, 0x04, 191]), bytes([1, 0x05, 191])]  # scroll None skipped
    assert by[(0x07, 0x03)].args[:2] == bytes([0x01, 0x2C])
    assert by[(0x07, 0x01)].args[0] == 0x0C
    # every storage-flag command uses VARSTORE
    for key in ((0x04, 0x05), (0x04, 0x06), (0x0F, 0x04)):
        assert all(r.args[0] == 0x01 for r in t.requests if (r.cmd_class, r.cmd_id) == key)
    # device mode untouched, read-only fields never written, dpi written after stages
    assert (0x00, 0x04) not in t.sent
    assert t.sent.index((0x04, 0x06)) < t.sent.index((0x04, 0x05))


def test_restore_reports_failed_and_missing_fields():
    from razertux.backup import restore_backup
    t = FakeTransport.full()
    del t.responses[(0x00, 0x05)]  # poll rate rejected
    data = dict(REAL, idle_time=None, low_battery_threshold=7000)
    failed = restore_backup(NagaV2Pro(t), data)
    assert failed == ["poll_rate", "idle_time", "low_battery_threshold"]
    assert (0x04, 0x05) in t.sent  # others still written


def test_restore_oserror_is_reported_not_raised():
    from razertux.backup import restore_backup
    t = FakeTransport.full()
    def boom(_):
        raise OSError("io")
    t.responses[(0x04, 0x05)] = boom
    assert restore_backup(NagaV2Pro(t), REAL) == ["dpi"]


def test_restore_empty_backup_reports_all_restorable_fields():
    from razertux.backup import restore_backup
    t = FakeTransport.full()
    assert restore_backup(NagaV2Pro(t), {}) == [
        "poll_rate", "dpi_stages", "dpi", "idle_time", "low_battery_threshold"]
    assert t.sent == []


def test_cli_restore_prefers_dbus(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".config/razertux").mkdir(parents=True)
    (tmp_path / ".config/razertux/mouse-backup.json").write_text(json.dumps(REAL))
    monkeypatch.setattr(cli, "_dbus_restore", lambda model="naga_v2_pro": [])

    def no_hidraw(path):
        raise AssertionError("must not open hidraw when the daemon answers")
    monkeypatch.setattr(cli, "_open_transport", no_hidraw)
    assert cli.main(["restore"]) == 0
    assert "Wiederhergestellt" in capsys.readouterr().out


def test_cli_restore_dbus_reports_partial(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_dbus_restore", lambda model="naga_v2_pro": ["poll_rate"])
    assert cli.main(["restore"]) == 2
    assert "poll_rate" in capsys.readouterr().err


def test_cli_restore_dbus_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))

    def boom(model):
        raise cli.RestoreError("Keine lesbare Sicherung")
    monkeypatch.setattr(cli, "_dbus_restore", boom)
    assert cli.main(["restore"]) == 1
    assert "Keine lesbare Sicherung" in capsys.readouterr().err


def test_cli_restore_falls_back_to_hidraw(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".config/razertux"
    d.mkdir(parents=True)
    (d / "mouse-backup.json").write_text(json.dumps(REAL))
    t = FakeTransport.full()
    monkeypatch.setattr(cli, "_dbus_restore", lambda model="naga_v2_pro": None)  # daemon not on the bus
    monkeypatch.setattr(cli, "_open_transport", lambda path: _Ctx(t))
    assert cli.main(["restore"]) == 0
    assert (0x04, 0x06) in t.sent and (0x00, 0x04) not in t.sent


def test_cli_restore_custom_path_skips_dbus(monkeypatch, tmp_path):
    p = tmp_path / "other.json"
    p.write_text(json.dumps(REAL))
    t = FakeTransport.full()
    monkeypatch.setattr(cli, "_dbus_restore", lambda model="naga_v2_pro": (_ for _ in ()).throw(AssertionError))
    monkeypatch.setattr(cli, "_open_transport", lambda path: _Ctx(t))
    assert cli.main(["restore", "--path", str(p)]) == 0
    assert (0x04, 0x05) in t.sent


def test_cli_restore_missing_backup_direct(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_dbus_restore", lambda model="naga_v2_pro": None)
    assert cli.main(["restore", "--path", str(tmp_path / "nope.json")]) == 1
    assert "Sicherung" in capsys.readouterr().err


def test_dbus_restore_returns_none_without_bus(monkeypatch, tmp_path):
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", f"unix:path={tmp_path}/nobus")
    assert cli._dbus_restore() is None


def test_restore_backup_limited_to_fields():
    from fake_transport import FakeTransport as FT
    from razertux.backup import restore_backup
    t = FT.full()
    data = {"poll_rate": 500, "dpi": [1800, 1800], "brightness": {"logo": 191},
            "idle_time": 300}
    assert restore_backup(NagaV2Pro(t), data, fields={"dpi", "dpi_stages"}) == ["dpi_stages"]
    assert t.sent == [(0x04, 0x05)]


# -- --model ------------------------------------------------------------------

import pytest  # noqa: E402


@pytest.fixture
def daemon_devices(monkeypatch):
    """Models the (fake) daemon reports as connected."""
    models = []
    monkeypatch.setattr(cli, "_connected_models", lambda: list(models))
    return models


def test_cli_model_defaults_to_the_only_connected_device(daemon_devices):
    daemon_devices.append("death_adder_v2")
    assert cli.resolve_model(None) == "death_adder_v2"


def test_cli_model_defaults_to_naga_without_daemon(daemon_devices):
    assert cli.resolve_model(None) == "naga_v2_pro"


def test_cli_model_explicit_wins(daemon_devices):
    daemon_devices[:] = ["naga_v2_pro", "death_adder_v2"]
    assert cli.resolve_model("death_adder_v2") == "death_adder_v2"


def test_cli_several_devices_need_model(daemon_devices, capsys):
    daemon_devices[:] = ["naga_v2_pro", "death_adder_v2"]
    for argv in (["status"], ["backup"], ["restore"]):
        assert cli.main(argv) == 1
        err = capsys.readouterr().err
        assert "--model" in err and "naga_v2_pro" in err and "death_adder_v2" in err


def test_cli_unknown_model(daemon_devices, capsys):
    assert cli.main(["status", "--model", "nope"]) == 1
    assert "Unbekanntes Modell" in capsys.readouterr().err


def test_cli_status_with_model_uses_that_devices_hidraw(daemon_devices, monkeypatch, capsys):
    seen = []

    def find(pids):
        seen.append(pids)
        return "/dev/null"
    monkeypatch.setattr(cli, "find_control_hidraw", find)
    monkeypatch.setattr(cli, "HidrawTransport", lambda path: _Ctx(FakeTransport.full()))
    assert cli.main(["status", "--model", "death_adder_v2"]) == 0
    assert seen and 0x0084 in seen[0]
    assert json.loads(capsys.readouterr().out)["poll_rate"] == 1000


def test_cli_backup_path_is_per_model(daemon_devices, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.default_backup_path("death_adder_v2") == (
        tmp_path / ".config/razertux/backups/death_adder_v2.json")


def test_cli_restore_asks_the_daemon_for_that_model(daemon_devices, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cli, "_dbus_restore", lambda model: seen.append(model) or [])
    assert cli.main(["restore", "--model", "death_adder_v2"]) == 0
    assert seen == ["death_adder_v2"]


def test_cli_restore_old_daemon_prints_version_hint(daemon_devices, monkeypatch, capsys):
    def old(model):
        raise cli.RestoreError(cli.VERSION_MISMATCH)
    monkeypatch.setattr(cli, "_dbus_restore", old)
    assert cli.main(["restore"]) == 1
    err = capsys.readouterr().err
    assert "Dienst-Version passt nicht" in err
    assert "systemctl --user restart razertuxd" in err  # I2: names the real remedy


# -- I4: experimental mice: PID of the opened node, probe before writing ------------

BV3 = "basilisk_v3_pro"  # experimental, 0x00aa wired / 0x00ab wireless


def _bad_probe_transport():
    t = FakeTransport.full()
    t.responses[(0x04, 0x85)] = bytes([0, 0xFF, 0xFF, 0xFF, 0xFF, 0, 0])  # DPI 65535
    return t


def _writes(t):
    return [s for s in t.sent if not s[1] & 0x80]


@pytest.fixture
def bv3(daemon_devices, monkeypatch, tmp_path):
    """--model basilisk_v3_pro, its hidraw node is /dev/hidraw7 (wireless PID)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_dbus_restore", lambda model: None)  # daemon not running
    monkeypatch.setattr(cli, "find_control_hidraw", lambda pids: "/dev/hidraw7")
    node = {"pid": 0x00AB, "t": FakeTransport.full(), "made": []}
    monkeypatch.setattr(cli, "hidraw_pid", lambda path: node["pid"])
    monkeypatch.setattr(cli, "HidrawTransport", lambda path: _Ctx(node["t"]))
    real = cli.make_mouse

    def spy(info, t, pid=None):
        node["made"].append(pid)
        return real(info, t, pid)
    monkeypatch.setattr(cli, "make_mouse", spy)
    return node


def test_cli_uses_the_pid_of_the_opened_node(bv3, capsys):
    assert cli.main(["status", "--model", BV3]) == 0
    assert bv3["made"] == [0x00AB]  # not the first PID of the model (0x00aa)


def test_cli_refuses_when_the_pid_is_unknown_for_a_multi_pid_model(bv3, capsys):
    bv3["pid"] = None
    assert cli.main(["backup", "--model", BV3]) == 1
    assert "Produkt-ID" in capsys.readouterr().err
    assert bv3["made"] == [] and bv3["t"].sent == []


def test_cli_backup_of_experimental_mouse_needs_a_passing_probe(bv3, tmp_path, capsys):
    bv3["t"] = _bad_probe_transport()
    assert cli.main(["backup", "--model", BV3]) == 1
    err = capsys.readouterr().err
    assert "Probe" in err and "nichts geschrieben" in err
    assert not cli.default_backup_path(BV3).exists()


def test_cli_offline_restore_of_experimental_mouse_needs_a_passing_probe(bv3, tmp_path,
                                                                         capsys):
    p = cli.default_backup_path(BV3)
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"dpi": [1600, 1600], "poll_rate": 500}))
    bv3["t"] = _bad_probe_transport()
    assert cli.main(["restore", "--model", BV3]) == 1
    assert "Probe" in capsys.readouterr().err
    assert _writes(bv3["t"]) == []
    bv3["t"] = FakeTransport.full()  # a sane mouse: restored
    assert cli.main(["restore", "--model", BV3]) == 2  # 2: fields missing in this small backup
    err = capsys.readouterr().err
    assert "Probe" not in err and "dpi," not in err and "poll_rate" not in err
    assert (0x04, 0x05) in _writes(bv3["t"])  # DPI written


def test_hidraw_pid_reads_the_usb_parent(monkeypatch):
    from types import SimpleNamespace

    from razertux.razer import transport
    usb = SimpleNamespace(attributes=SimpleNamespace(asstring=lambda k: {"idProduct": "00ab"}[k]))
    dev = SimpleNamespace(find_parent=lambda sub, typ: usb if typ == "usb_device" else None)
    seen = []

    class Devices:
        @staticmethod
        def from_device_file(ctx, path):
            seen.append(path)
            return dev
    monkeypatch.setattr(transport.pyudev, "Devices", Devices)
    assert transport.hidraw_pid("/dev/hidraw7") == 0x00AB
    assert seen == ["/dev/hidraw7"]
    assert transport.hidraw_pid("/dev/null") is None  # not a hidraw node: no udev lookup
    assert seen == ["/dev/hidraw7"]
