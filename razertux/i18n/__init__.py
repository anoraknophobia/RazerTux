"""Translations without Qt tooling: JSON catalogs `<lang>.json` next to this file,
structured {"<context>": {"<German source>": "<translation>"}}. German is the
source language and needs no catalog.

This module is plain Python (the daemon and the config store use `tr()`); the GUI
installs `razertux.i18n.qt_translator.JsonTranslator`, which reads the same
catalogs, so self.tr()/QCoreApplication.translate() work as usual."""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

SUPPORTED = ("de", "en", "es", "fr")
CHOICES = ("system", *SUPPORTED)
SOURCE_LANGUAGE = "de"
FALLBACK = "en"  # system language not supported
CATALOG_DIR = Path(__file__).parent

_current = SOURCE_LANGUAGE


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, dict[str, str]]:
    """The catalog of `lang` ({} for German or a missing/broken file)."""
    if lang == SOURCE_LANGUAGE:
        return {}
    try:
        data = json.loads((CATALOG_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def lookup(lang: str, context: str, text: str) -> str | None:
    """Translation of `text` or None (caller falls back to the source)."""
    found = catalog(lang).get(context, {}).get(text)
    return found or None


def system_language() -> str | None:
    """Two-letter language of the POSIX locale environment (None for C/POSIX)."""
    for var in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "")
        if var == "LANGUAGE":
            value = value.split(":")[0]
        if value:
            lang = value.split(".")[0].split("_")[0].lower()
            return None if lang in ("c", "posix") else lang
    return None


def resolve_language(setting: str, system: str | None = None) -> str:
    """Settings.language -> a supported language. "system" (or anything unknown)
    uses `system` (e.g. QLocale.system().name()) or the environment's locale and
    falls back to English if that language is not supported."""
    if setting in SUPPORTED:
        return setting
    name = system if system is not None else (system_language() or "")
    lang = name[:2].lower()
    return lang if lang in SUPPORTED else FALLBACK


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in SUPPORTED else SOURCE_LANGUAGE


def current_language() -> str:
    return _current


def apply_settings(settings: Any, system: str | None = None) -> str:
    """Set the process language from Settings.language; returns it."""
    set_language(resolve_language(getattr(settings, "language", "system"), system))
    return _current


def tr(context: str, text: str, **fmt: Any) -> str:
    """Translate `text` into the current language and fill its {name} placeholders."""
    out = lookup(_current, context, text) or text
    return out.format(**fmt) if fmt else out
