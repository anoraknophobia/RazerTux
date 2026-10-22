"""On-disk storage for settings, profiles and macros (JSON, atomic writes)."""
from __future__ import annotations

import json
import os
import re
import secrets
import unicodedata
from pathlib import Path
from typing import Any

from .model import Macro, Profile, Settings, default_profile


_SAFE_ID = re.compile(r"[a-z0-9-]+")
_READ_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError)


def new_profile_id(name: str) -> str:
    """Filesystem-safe id: slug of the name plus a short random suffix."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:32].strip("-")
    return f"{slug or 'profil'}-{secrets.token_hex(3)}"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class ConfigStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.errors: list[str] = []

    @property
    def _profiles_dir(self) -> Path:
        return self.root / "profiles"

    def _profile_path(self, profile_id: str) -> Path:
        if not _SAFE_ID.fullmatch(profile_id):
            raise ValueError(f"Ungültige Profil-ID: {profile_id!r}")
        return self._profiles_dir / f"{profile_id}.json"

    def _read(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _error(self, path: Path, exc: Exception) -> None:
        self.errors.append(f"{path.name} ist beschädigt und wurde ignoriert: {exc}")

    # settings
    def load_settings(self) -> Settings:
        path = self.root / "settings.json"
        if path.exists():
            try:
                return Settings.from_dict(self._read(path))
            except _READ_ERRORS as e:
                self._error(path, e)
        return Settings(active_profile="default")

    def save_settings(self, s: Settings) -> None:
        _write_json(self.root / "settings.json", s.to_dict())

    # profiles
    def default_profile(self) -> Profile:
        return default_profile()

    def _load_path(self, path: Path) -> Profile:
        return Profile.from_dict(self._read(path))

    def list_profiles(self) -> list[Profile]:
        profiles: list[Profile] = []
        if self._profiles_dir.is_dir():
            for path in sorted(self._profiles_dir.glob("*.json")):
                try:
                    profiles.append(self._load_path(path))
                except _READ_ERRORS as e:
                    self._error(path, e)
        return profiles or [default_profile()]

    def load_profile(self, profile_id: str) -> Profile:
        """Load a profile; on a missing/corrupt file record an error and
        return the default profile."""
        try:
            path = self._profile_path(profile_id)
        except ValueError as e:
            self.errors.append(f"Profil {profile_id!r} nicht lesbar: {e}")
            return default_profile()
        if not path.exists():
            if profile_id != "default":
                self.errors.append(f"{path.name} fehlt, Standardprofil wird verwendet")
            return default_profile()
        try:
            return self._load_path(path)
        except _READ_ERRORS as e:
            self._error(path, e)
            return default_profile()

    def save_profile(self, p: Profile) -> None:
        _write_json(self._profile_path(p.id), p.to_dict())

    def delete_profile(self, profile_id: str) -> None:
        self._profile_path(profile_id).unlink(missing_ok=True)

    # macros
    def load_macros(self) -> dict[str, Macro]:
        path = self.root / "macros.json"
        if not path.exists():
            return {}
        try:
            return {k: Macro.from_dict(v) for k, v in self._read(path).items()}
        except _READ_ERRORS as e:
            self._error(path, e)
            return {}

    def save_macros(self, macros: dict[str, Macro]) -> None:
        _write_json(self.root / "macros.json", {k: m.to_dict() for k, m in macros.items()})

    # import / export
    def export_profile(self, profile_id: str, path: Path) -> None:
        src = self._profile_path(profile_id)
        if src.exists():
            try:
                profile = self._load_path(src)
            except _READ_ERRORS as e:
                raise ValueError(f"{src.name} ist beschädigt und kann nicht exportiert werden: {e}") from e
        elif profile_id == "default":
            profile = default_profile()
        else:
            raise ValueError(f"{src.name} fehlt und kann nicht exportiert werden")
        _write_json(Path(path), profile.to_dict())

    def import_profile(self, path: Path) -> Profile:
        p = self._load_path(Path(path))
        if (not _SAFE_ID.fullmatch(p.id) or p.id == "default"
                or self._profile_path(p.id).exists()):
            p.id = new_profile_id(p.name)
        self.save_profile(p)
        return p
