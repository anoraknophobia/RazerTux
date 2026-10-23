"""~/.config/naga-config -> ~/.config/razertux: copied once, never overwritten,
the old directory stays untouched. Everything runs in tmp dirs (HOME is faked)."""
import json
import logging
import os
from pathlib import Path

import pytest

from razertux import cli
from razertux.config import migrate
from razertux.daemon import main as daemon_main
from razertux.gui import app as gui_app


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _old_tree(home):
    old = home / ".config" / "naga-config"
    (old / "profiles").mkdir(parents=True)
    (old / "macros").mkdir()
    (old / "profiles" / "default.json").write_text('{"id": "default", "name": "Standard"}')
    (old / "macros" / "m1.json").write_text('{"id": "m1"}')
    (old / "settings.json").write_text('{"active_profile": "default", "language": "en"}')
    (old / "state.json").write_text('{"scroll_stage": 2}')
    (old / "mouse-backup.json").write_text('{"dpi": [800, 800]}')
    return old


def _snapshot(root):
    return {p.relative_to(root): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*"))}


def test_default_dirs(home):
    assert migrate.default_config_dir() == home / ".config" / "razertux"
    assert migrate.legacy_config_dir() == home / ".config" / "naga-config"
    assert daemon_main.default_config_dir() == home / ".config" / "razertux"


def test_copies_the_whole_directory_and_leaves_the_old_one(home, caplog):
    old = _old_tree(home)
    before = _snapshot(old)
    with caplog.at_level(logging.INFO, logger="razertux.migrate"):
        assert migrate.migrate_legacy_config() is True
    new = home / ".config" / "razertux"
    assert sorted(p.relative_to(new) for p in new.rglob("*")) == sorted(before)
    for rel, (data, _) in before.items():
        if data is not None:
            assert (new / rel).read_bytes() == data
    assert _snapshot(old) == before  # untouched, same mtimes
    assert [r.getMessage() for r in caplog.records if "naga-config" in r.getMessage()]
    assert not list((home / ".config").glob("razertux.*"))  # no temp dir left behind


def test_second_start_does_nothing(home, caplog):
    _old_tree(home)
    assert migrate.migrate_legacy_config() is True
    (home / ".config" / "naga-config" / "settings.json").write_text('{"language": "fr"}')
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="razertux.migrate"):
        assert migrate.migrate_legacy_config() is False
    assert caplog.records == []  # logged only once
    assert json.loads((home / ".config/razertux/settings.json").read_text())["language"] == "en"


def test_never_overwrites_an_existing_new_dir(home):
    _old_tree(home)
    new = home / ".config" / "razertux"
    new.mkdir(parents=True)
    (new / "settings.json").write_text('{"language": "es"}')
    assert migrate.migrate_legacy_config() is False
    assert sorted(p.name for p in new.iterdir()) == ["settings.json"]
    assert json.loads((new / "settings.json").read_text()) == {"language": "es"}


def test_even_an_empty_new_dir_wins(home):
    _old_tree(home)
    (home / ".config" / "razertux").mkdir(parents=True)
    assert migrate.migrate_legacy_config() is False
    assert list((home / ".config" / "razertux").iterdir()) == []


def test_nothing_to_migrate(home):
    assert migrate.migrate_legacy_config() is False
    assert not (home / ".config" / "razertux").exists()


def test_old_path_that_is_a_file_is_ignored(home):
    (home / ".config").mkdir()
    (home / ".config" / "naga-config").write_text("x")
    assert migrate.migrate_legacy_config() is False
    assert not (home / ".config" / "razertux").exists()


def test_failed_copy_leaves_no_half_new_dir(home, monkeypatch):
    _old_tree(home)

    def broken(src, dst, **kw):
        os.makedirs(dst)
        Path(dst, "half").write_text("x")
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(migrate.shutil, "copytree", broken)
    assert migrate.migrate_legacy_config() is False
    assert not (home / ".config" / "razertux").exists()
    assert not list((home / ".config").glob("razertux*"))


def test_lost_race_keeps_the_winner(home, monkeypatch):
    """Daemon and GUI start together: whoever renames first wins, the other backs off."""
    _old_tree(home)
    real_copytree = migrate.shutil.copytree

    def copy_then_other_process_wins(src, dst, **kw):
        monkeypatch.setattr(migrate.shutil, "copytree", real_copytree)  # recursion: real one
        real_copytree(src, dst, **kw)
        winner = home / ".config" / "razertux"
        winner.mkdir()
        (winner / "settings.json").write_text('{"language": "winner"}')
    monkeypatch.setattr(migrate.shutil, "copytree", copy_then_other_process_wins)
    assert migrate.migrate_legacy_config() is False
    new = home / ".config" / "razertux"
    assert json.loads((new / "settings.json").read_text()) == {"language": "winner"}
    assert not list((home / ".config").glob("razertux.*"))


# -- every entry point migrates before it reads the default dir ------------------

def test_daemon_migrates_before_running(home, monkeypatch):
    _old_tree(home)
    seen = []

    async def fake_run(config_dir):
        seen.append((config_dir, (config_dir / "settings.json").exists()))
        return 0
    monkeypatch.setattr(daemon_main, "run", fake_run)
    assert daemon_main.main([]) == 0
    assert seen == [(home / ".config" / "razertux", True)]


def test_daemon_with_explicit_config_dir_does_not_migrate(home, monkeypatch, tmp_path):
    _old_tree(home)

    async def fake_run(config_dir):
        return 0
    monkeypatch.setattr(daemon_main, "run", fake_run)
    assert daemon_main.main(["--config-dir", str(tmp_path / "x")]) == 0
    assert not (home / ".config" / "razertux").exists()


def test_gui_migrates_first(home, monkeypatch):
    _old_tree(home)
    calls = []
    monkeypatch.setattr(gui_app, "migrate_legacy_config", lambda: calls.append("migrate") or True)
    with pytest.raises(SystemExit):
        gui_app.main(["--help"])
    assert calls == ["migrate"]


def test_gui_with_explicit_config_dir_does_not_migrate(home, monkeypatch):
    calls = []
    monkeypatch.setattr(gui_app, "migrate_legacy_config", lambda: calls.append("migrate"))
    with pytest.raises(SystemExit):
        gui_app.main(["--config-dir", "/nonexistent", "--help"])
    assert calls == []


def test_cli_restore_finds_the_migrated_backup(home, monkeypatch):
    _old_tree(home)
    monkeypatch.setattr(cli, "_dbus_restore", lambda: None)
    loaded = []
    monkeypatch.setattr(cli, "load_backup", lambda p: loaded.append(p) or (_ for _ in ()).throw(OSError("stop")))
    cli.main(["restore"])
    assert loaded == [home / ".config" / "razertux" / "backups" / "naga_v2_pro.json"]
    assert (home / ".config" / "razertux" / "mouse-backup.json").is_file()
    assert (home / ".config" / "razertux" / "backups" / "naga_v2_pro.json").is_file()


# --- schema 2 -------------------------------------------------------------

import shutil

from razertux.config import ConfigStore

V1 = Path(__file__).parent / "data" / "config_v1"


def _tree_snapshot(root):
    return {str(p.relative_to(root)): (p.read_bytes() if p.is_file() else None)
            for p in sorted(root.rglob("*"))}


@pytest.fixture
def v1(tmp_path):
    root = tmp_path / "cfg"
    shutil.copytree(V1, root)
    return root


def test_schema2_migrates_real_user_config(v1):
    originals = {f.name: f.read_bytes() for f in (V1 / "profiles").glob("*.json")}
    before = {n: (v1 / n).read_bytes() for n in ("macros.json", "state.json", "mouse-backup.json")}
    old_settings = (v1 / "settings.json").read_bytes()

    assert migrate.migrate_schema_2(v1) is True

    ms = ConfigStore(v1).for_model("naga_v2_pro")
    for name, raw in originals.items():
        pid = name[:-5]
        assert (v1 / "profiles" / "naga_v2_pro" / name).read_bytes() == raw
        assert ms.load_profile(pid).to_dict() == json.loads(raw)
    assert {p.id for p in ms.list_profiles()} == {"test", "test2-73cb63"}
    settings = ConfigStore(v1).load_settings()
    assert settings.active_profile == "test2-73cb63"
    assert json.loads((v1 / "settings.json").read_text())["active_profiles"] == \
        {"naga_v2_pro": "test2-73cb63"}
    assert (v1 / "backups" / "naga_v2_pro.json").read_bytes() == before["mouse-backup.json"]
    for n in ("macros.json", "state.json"):
        assert (v1 / n).read_bytes() == before[n]
    assert ConfigStore(v1).load_macros() is not None
    assert (v1 / "pre-schema2" / "settings.json").read_bytes() == old_settings
    for name, raw in originals.items():
        assert (v1 / "pre-schema2" / "profiles" / name).read_bytes() == raw
    assert settings.dpi_limit == 10000 and settings.scroll_stage_names[1] == "Ultra-fine"

    snap = _tree_snapshot(v1)
    assert migrate.migrate_schema_2(v1) is False
    assert _tree_snapshot(v1) == snap


def test_schema2_never_overwrites_existing_targets(v1):
    (v1 / "backups").mkdir()
    (v1 / "backups" / "naga_v2_pro.json").write_text("{}")
    (v1 / "profiles" / "naga_v2_pro").mkdir()
    (v1 / "profiles" / "naga_v2_pro" / "test.json").write_text("{\"keep\": 1}")
    migrate.migrate_schema_2(v1)
    assert (v1 / "backups" / "naga_v2_pro.json").read_text() == "{}"
    assert (v1 / "profiles" / "naga_v2_pro" / "test.json").read_text() == "{\"keep\": 1}"


def test_schema2_fresh_or_corrupt_root_is_left_alone(tmp_path):
    assert migrate.migrate_schema_2(tmp_path / "none") is False
    assert not (tmp_path / "none").exists()
    (tmp_path / "settings.json").write_text("{broken")
    assert migrate.migrate_schema_2(tmp_path) is False
    assert (tmp_path / "settings.json").read_text() == "{broken"


def test_schema2_runs_at_startup(home, monkeypatch):
    root = home / ".config" / "razertux"
    shutil.copytree(V1, root)
    monkeypatch.setattr(gui_app, "migrate_schema_2", migrate.migrate_schema_2)
    monkeypatch.setattr(daemon_main, "migrate_schema_2", migrate.migrate_schema_2)
    assert hasattr(gui_app, "migrate_schema_2") and hasattr(daemon_main, "migrate_schema_2")
