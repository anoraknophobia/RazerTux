import json

from fake_transport import FakeTransport

from naga_config import cli
from naga_config.backup import load_backup, save_backup
from naga_config.razer.naga_v2_pro import NagaV2Pro


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
    assert cli.default_backup_path() == tmp_path / ".config/naga-config/mouse-backup.json"


def test_backup_refuses_when_core_fields_missing(tmp_path):
    import pytest
    from naga_config.backup import BackupError
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
    del t.responses[(0x02, 0x98)]
    monkeypatch.setattr(cli, "_open_transport", lambda path: _Ctx(t))
    assert cli.main(["backup", "--path", str(tmp_path / "bk.json")]) == 0
    assert "scroll_profile" in capsys.readouterr().err
