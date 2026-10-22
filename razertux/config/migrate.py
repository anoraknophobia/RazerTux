"""One-time move of the configuration from ~/.config/naga-config (the project's
old name) to ~/.config/razertux.

The old directory is copied, never moved or changed, so the old package keeps
working until it is removed. An existing new directory always wins, even an
empty one. The copy goes to a temporary sibling first and is renamed into place,
so a daemon and a GUI starting at the same time cannot produce a half copy."""
from __future__ import annotations

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
