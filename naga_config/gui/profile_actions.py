"""Profile operations of the main window (new, duplicate, rename, delete, export,
import). A mixin for MainWindow; translation context stays "MainWindow"."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QInputDialog

from naga_config.config.model import Profile, default_profile
from naga_config.config.store import new_profile_id


class ProfileActions:
    """Needs from the window: store, client, profile, profile_combo, tr(), _warn(),
    _confirm(), _resolve_unsaved(), _show_profile(), select_profile(),
    reload_profile_list(), _update_dirty()."""

    def _ask_name(self, title: str, default: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, self.tr("Name des Profils:"), text=default)
        return name.strip() if ok and name.strip() else None

    def _add_profile(self, profile: Profile) -> None:
        self.store.save_profile(profile)
        self.reload_profile_list()
        self._show_profile(profile)
        self.client.reload()

    def new_profile(self) -> None:
        name = self._ask_name(self.tr("Neues Profil")) if self._resolve_unsaved() else None
        if name:
            self._add_profile(Profile(id=new_profile_id(name), name=name,
                                      standard=default_profile().standard))

    def duplicate_profile(self) -> None:
        name = self._ask_name(self.tr("Profil duplizieren"),
                                    self.tr("%1 (Kopie)").replace("%1", self.profile.name)) \
            if self._resolve_unsaved() else None
        if name:
            copy_ = Profile.from_dict(self.profile.to_dict())
            copy_.id, copy_.name = new_profile_id(name), name
            self._add_profile(copy_)

    def rename_profile(self) -> None:
        name = self._ask_name(self.tr("Profil umbenennen"), self.profile.name)
        if name:
            self.profile.name = name
            self.profile_combo.setItemText(self.profile_combo.currentIndex(), name)
            self._update_dirty()

    def delete_profile(self) -> None:
        if len(self.store.list_profiles()) < 2:
            self._warn(self.tr("Löschen nicht möglich"), self.tr("Das letzte Profil kann nicht gelöscht werden."))
            return
        if not self._confirm(self.tr("Profil löschen"),
                             self.tr("Profil „%1“ wirklich löschen?").replace("%1", self.profile.name)):
            return
        self.store.delete_profile(self.profile.id)
        self.reload_profile_list()
        self.select_profile(self.profile_combo.itemData(0), force=True)
        self.client.reload()

    def export_profile(self) -> None:
        if not self._resolve_unsaved():
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Profil exportieren"),
                                              f"{self.profile.name}.json", self.tr("Profil (*.json)"))
        if path:
            try:
                self.store.export_profile(self.profile.id, Path(path))
            except (OSError, ValueError) as e:
                self._warn(self.tr("Export fehlgeschlagen"),
                           self.tr("Das Profil konnte nicht exportiert werden:") + f"\n{e}")

    def import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Profil importieren"), "", self.tr("Profil (*.json)"))
        if path:
            self.import_profile_from(Path(path))

    def import_profile_from(self, path: Path) -> None:
        if not self._resolve_unsaved():
            return
        try:
            profile = self.store.import_profile(path)
        except Exception as e:  # corrupt JSON, wrong structure, unreadable file
            self._warn(self.tr("Import fehlgeschlagen"),
                       self.tr("Die Datei „%1“ ist kein gültiges Profil und wurde nicht importiert.")
                       .replace("%1", path.name) + f"\n\n{e}")
            return
        self.reload_profile_list()
        self._show_profile(profile)
        self.client.reload()
