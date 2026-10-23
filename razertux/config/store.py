"""On-disk storage for settings, profiles and macros (JSON, atomic writes)."""
from __future__ import annotations

import json
import os
import re
import secrets
import unicodedata
from pathlib import Path
from typing import Any

from razertux.i18n import tr

from razertux.devices.db import load_devices

from .model import (
    NAGA_MODEL, DeviceSettings, Macro, Profile, Settings, default_profile, default_profile_for,
)


_SAFE_ID = re.compile(r"[a-z0-9-]+")
_SAFE_MODEL = re.compile(r"[a-z0-9_]+")
_READ_ERRORS = (OSError, ValueError, KeyError, TypeError, AttributeError)


def _slug_id(name: str, fallback: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:32].strip("-")
    return f"{slug or fallback}-{secrets.token_hex(3)}"


def new_profile_id(name: str) -> str:
    """Filesystem-safe id: slug of the name plus a short random suffix."""
    return _slug_id(name, "profil")


def new_macro_id(name: str) -> str:
    """Macro id: slug of the name plus a short random suffix."""
    return _slug_id(name, "makro")


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class ConfigStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.errors: list[str] = []

    def _read(self, path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def _error(self, path: Path, exc: Exception) -> None:
        self.errors.append(tr("store", "{file} is corrupt and was ignored: {error}",
                              file=path.name, error=exc))

    def for_model(self, model: str) -> ModelStore:
        return ModelStore(self, model)

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

    # profiles of the Naga V2 Pro (thin delegates until daemon and GUI are per-model)
    def default_profile(self) -> Profile:
        return self.for_model(NAGA_MODEL).default_profile()

    def list_profiles(self) -> list[Profile]:
        return self.for_model(NAGA_MODEL).list_profiles()

    def load_profile(self, profile_id: str) -> Profile:
        return self.for_model(NAGA_MODEL).load_profile(profile_id)

    def save_profile(self, p: Profile) -> None:
        self.for_model(NAGA_MODEL).save_profile(p)

    def delete_profile(self, profile_id: str) -> None:
        self.for_model(NAGA_MODEL).delete_profile(profile_id)

    def export_profile(self, profile_id: str, path: Path) -> None:
        self.for_model(NAGA_MODEL).export_profile(profile_id, path)

    def import_profile(self, path: Path) -> Profile:
        return self.for_model(NAGA_MODEL).import_profile(path)

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


class ModelStore:
    """Profiles (profiles/<model>/<id>.json) and device settings
    (devices/<model>.json) of one device model."""

    def __init__(self, store: ConfigStore, model: str):
        if not _SAFE_MODEL.fullmatch(model):
            raise ValueError(f"invalid device model: {model!r}")
        self._store = store
        self.model = model

    @property
    def errors(self) -> list[str]:
        return self._store.errors

    @property
    def config(self) -> ConfigStore:
        """The whole configuration (settings, macros) this model's store belongs to."""
        return self._store

    @property
    def root(self) -> Path:
        return self._store.root

    @property
    def _profiles_dir(self) -> Path:
        return self._store.root / "profiles" / self.model

    @property
    def _device_path(self) -> Path:
        return self._store.root / "devices" / f"{self.model}.json"

    def _profile_path(self, profile_id: str) -> Path:
        if not _SAFE_ID.fullmatch(profile_id):
            raise ValueError(tr("store", "Invalid profile ID: {profile}", profile=repr(profile_id)))
        return self._profiles_dir / f"{profile_id}.json"

    def _read(self, path: Path) -> Any:
        return self._store._read(path)

    def _error(self, path: Path, exc: Exception) -> None:
        self._store._error(path, exc)

    def default_profile(self) -> Profile:
        info = load_devices().get(self.model)
        return default_profile_for(info) if info is not None else \
            Profile(id="default", name="Standard", standard=default_profile().standard)

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
        return profiles or [self.default_profile()]

    def load_profile(self, profile_id: str) -> Profile:
        """Load a profile; on a missing/corrupt file record an error and
        return the default profile."""
        try:
            path = self._profile_path(profile_id)
        except ValueError as e:
            self.errors.append(tr("store", "Profile {profile} not readable: {error}",
                                  profile=repr(profile_id), error=e))
            return self.default_profile()
        if not path.exists():
            if profile_id != "default":
                self.errors.append(tr("store", "{file} is missing, using the default profile", file=path.name))
            return self.default_profile()
        try:
            return self._load_path(path)
        except _READ_ERRORS as e:
            self._error(path, e)
            return self.default_profile()

    def save_profile(self, p: Profile) -> None:
        _write_json(self._profile_path(p.id), p.to_dict())

    def delete_profile(self, profile_id: str) -> None:
        self._profile_path(profile_id).unlink(missing_ok=True)

    def export_profile(self, profile_id: str, path: Path) -> None:
        src = self._profile_path(profile_id)
        if src.exists():
            try:
                profile = self._load_path(src)
            except _READ_ERRORS as e:
                raise ValueError(tr("store", "{file} is corrupt and cannot be exported: {error}",
                                    file=src.name, error=e)) from e
        elif profile_id == "default":
            profile = self.default_profile()
        else:
            raise ValueError(tr("store", "{file} is missing and cannot be exported", file=src.name))
        _write_json(Path(path), profile.to_dict())

    def import_profile(self, path: Path) -> Profile:
        p = self._load_path(Path(path))
        if (not _SAFE_ID.fullmatch(p.id) or p.id == "default"
                or self._profile_path(p.id).exists()):
            p.id = new_profile_id(p.name)
        self.save_profile(p)
        return p

    # device settings
    def _remap_default(self) -> bool:
        info = load_devices().get(self.model)
        return bool(info and info.verified)

    def load_device_settings(self) -> DeviceSettings:
        path = self._device_path
        if path.exists():
            try:
                return DeviceSettings.from_dict(self._read(path), remap_default=self._remap_default())
            except _READ_ERRORS as e:
                self._error(path, e)
        return DeviceSettings(remap_enabled=self._remap_default())

    def save_device_settings(self, ds: DeviceSettings) -> None:
        _write_json(self._device_path, ds.to_dict())
