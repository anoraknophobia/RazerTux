"""Profile operations of the main window (new, duplicate, rename, delete, export,
import, macro deletion, activation). A mixin for MainWindow; translation context stays "MainWindow"."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from razertux import APP_NAME
from razertux.config.model import Profile
from razertux.config.store import ModelStore, new_profile_id
from razertux.gui.macro_tab import strip_macro


class ProfileActions:
    """Needs from the window: store, profiles (ModelStore of the shown mouse), client,
    profile, macros, profile_combo, tr(), _warn(), _confirm(), _resolve_unsaved(),
    _show_profile(), select_profile(), reload_profile_list(), _update_dirty(), _refresh()."""

    def _ask_name(self, title: str, default: str = "") -> str | None:
        name, ok = QInputDialog.getText(self, title, self.tr("Profile name:"), text=default)
        return name.strip() if ok and name.strip() else None

    def _add_profile(self, profile: Profile) -> None:
        self.profiles.save_profile(profile)
        self.reload_profile_list()
        self._show_profile(profile)
        self.client.reload()

    def new_profile(self) -> None:
        name = self._ask_name(self.tr("New profile")) if self._resolve_unsaved() else None
        if name:
            self._add_profile(Profile(id=new_profile_id(name), name=name,
                                      standard=self.profiles.default_profile().standard))

    def duplicate_profile(self) -> None:
        name = self._ask_name(self.tr("Duplicate profile"),
                                    self.tr("%1 (copy)").replace("%1", self.profile.name)) \
            if self._resolve_unsaved() else None
        if name:
            copy_ = Profile.from_dict(self.profile.to_dict())
            copy_.id, copy_.name = new_profile_id(name), name
            self._add_profile(copy_)

    def rename_profile(self) -> None:
        name = self._ask_name(self.tr("Rename profile"), self.profile.name)
        if name:
            self.profile.name = name
            self.profile_combo.setItemText(self.profile_combo.currentIndex(), name)
            self._update_dirty()

    def delete_profile(self) -> None:
        if len(self.profiles.list_profiles()) < 2:
            self._warn(self.tr("Cannot delete"), self.tr("The last profile cannot be deleted."))
            return
        if not self._confirm(self.tr("Delete profile"),
                             self.tr("Really delete profile “%1”?").replace("%1", self.profile.name)):
            return
        self.profiles.delete_profile(self.profile.id)
        self.reload_profile_list()
        self.select_profile(self.profile_combo.itemData(0), force=True)
        self.client.reload()

    def export_profile(self) -> None:
        if not self._resolve_unsaved():
            return
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Export profile"),
                                              f"{self.profile.name}.json", self.tr("Profile (*.json)"))
        if path:
            try:
                self.profiles.export_profile(self.profile.id, Path(path))
            except (OSError, ValueError) as e:
                self._warn(self.tr("Export failed"),
                           self.tr("The profile could not be exported:") + f"\n{e}")

    def import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Import profile"), "", self.tr("Profile (*.json)"))
        if path:
            self.import_profile_from(Path(path))

    def import_profile_from(self, path: Path) -> None:
        if not self._resolve_unsaved():
            return
        try:
            profile = self.profiles.import_profile(path)
        except Exception as e:  # corrupt JSON, wrong structure, unreadable file
            self._warn(self.tr("Import failed"),
                       self.tr("The file “%1” is not a valid profile and was not imported.")
                       .replace("%1", path.name) + f"\n\n{e}")
            return
        self.reload_profile_list()
        self._show_profile(profile)
        self.client.reload()

    def _ask_unsaved(self) -> str:
        box = QMessageBox(QMessageBox.Icon.Question, APP_NAME,
                          self.tr("The profile has unsaved changes."), parent=self)
        save = box.addButton(self.tr("Save"), QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(self.tr("Discard"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(self.tr("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return {save: "save", discard: "discard"}.get(box.clickedButton(), "cancel")

    def _on_macro_deleted(self, macro_id: str) -> None:
        """Deleted macro: reset its bindings in every profile and drop it from disk.

        Other unsaved macro edits stay unsaved (only this id leaves disk, memory and baseline)."""
        try:
            for ms in self._all_model_stores():  # on-disk state of every mouse (the open profile too)
                for p in ms.list_profiles():
                    strip_macro(p, macro_id)
                    ms.save_profile(p)
            on_disk = self.store.load_macros()
            on_disk.pop(macro_id, None)
            self.store.save_macros(on_disk)
        except OSError as e:
            self._warn(self.tr("Saving failed"),
                       self.tr("The macro could not be deleted:") + f"\n{e}")
            return
        strip_macro(self.profile, macro_id)  # unsaved edits stay, only the binding goes
        base = Profile.from_dict(self._baseline)
        strip_macro(base, macro_id)
        self._baseline = base.to_dict()
        self._macros_baseline.pop(macro_id, None)
        self.client.reload()
        self._refresh()

    def activate_current(self) -> None:
        if self._resolve_unsaved():
            self._ask(self.client.activate_profile, self.profile.id, self.model, default=False)

    def _all_model_stores(self) -> list[ModelStore]:
        """The shown mouse and every mouse with saved profiles."""
        root = self.store.root / "profiles"
        models = {self.profiles.model} | ({d.name for d in root.iterdir() if d.is_dir()} if root.is_dir() else set())
        return [self.store.for_model(m) for m in sorted(models)]
