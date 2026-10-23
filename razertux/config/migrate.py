"""One-time move of the configuration from ~/.config/naga-config (the project's
old name) to ~/.config/razertux.

The old directory is copied, never moved or changed, so the old package keeps
working until it is removed. An existing new directory always wins, even an
empty one. The copy goes to a temporary sibling first and is renamed into place,
so a daemon and a GUI starting at the same time cannot produce a half copy."""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger("razertux.migrate")

DIR_NAME = "razertux"
LEGACY_DIR_NAME = "naga-config"


def default_config_dir() -> Path:
    return Path.home() / ".config" / DIR_NAME


def legacy_config_dir() -> Path:
    return Path.home() / ".config" / LEGACY_DIR_NAME


def migrate_legacy_config() -> bool:
    """Copy ~/.config/naga-config to ~/.config/razertux if only the old one exists.
    Returns True if it copied (which happens at most once)."""
    new, old = default_config_dir(), legacy_config_dir()
    if new.exists() or new.is_symlink() or not old.is_dir():
        return False
    tmp = new.with_name(f"{new.name}.migrating-{os.getpid()}")
    try:
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(old, tmp, symlinks=True)
        os.rename(tmp, new)  # fails if another process created `new` meanwhile
    except OSError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        if not new.exists():
            logger.warning("Konfiguration aus %s nicht übernommen: %s", old, e)
        return False
    logger.info("Konfiguration aus %s nach %s kopiert (der alte Ordner bleibt unverändert)",
                old, new)
    return True


NAGA_MODEL = "naga_v2_pro"


def _copy_new(src: Path, dst: Path) -> None:
    """Copy a file unless the target exists (never overwrites)."""
    if src.is_file() and not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def migrate_schema_2(root: Path) -> bool:
    """Config schema 1 -> 2 in `root`: profiles/*.json -> profiles/naga_v2_pro/,
    mouse-backup.json -> backups/naga_v2_pro.json (a copy), settings.json gets
    "schema": 2 and "active_profiles". The old files are copied to pre-schema2/
    and never deleted; existing targets are never overwritten. settings.json is
    rewritten last, so an interrupted run is simply repeated. Returns True if
    it migrated."""
    root = Path(root)
    settings_path = root / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8")) \
            if settings_path.exists() else {}
    except (OSError, ValueError) as e:
        logger.warning("%s nicht lesbar, Schema-2-Migration übersprungen: %s", settings_path, e)
        return False
    if not isinstance(settings, dict) or settings.get("schema") == 2:
        return False
    old_profiles = sorted((root / "profiles").glob("*.json")) if (root / "profiles").is_dir() else []
    backup = root / "mouse-backup.json"
    if not settings and not old_profiles and not backup.is_file():
        return False  # fresh install: nothing to migrate
    try:
        saved = root / "pre-schema2"
        _copy_new(settings_path, saved / "settings.json")
        _copy_new(backup, saved / "mouse-backup.json")
        for p in old_profiles:
            _copy_new(p, saved / "profiles" / p.name)
            _copy_new(p, root / "profiles" / NAGA_MODEL / p.name)
        _copy_new(backup, root / "backups" / f"{NAGA_MODEL}.json")
        active = settings.pop("active_profile", None)
        new = {"schema": 2}
        active_profiles = settings.get("active_profiles")
        if not isinstance(active_profiles, dict):
            active_profiles = {}
        if isinstance(active, str):
            active_profiles.setdefault(NAGA_MODEL, active)
        new["active_profiles"] = active_profiles
        settings.pop("active_profiles", None)
        new.update(settings)
        tmp = settings_path.with_name(settings_path.name + ".tmp")
        tmp.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, settings_path)
    except OSError as e:
        logger.warning("Schema-2-Migration von %s fehlgeschlagen: %s", root, e)
        return False
    logger.info("Konfiguration %s auf Schema 2 migriert (Originale in pre-schema2/)", root)
    return True
