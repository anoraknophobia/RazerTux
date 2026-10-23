"""Device handling of the main window: which mouse is shown, its tabs, the
experimental banner and "Learn button". A mixin for MainWindow (no texts of its
own; the dialogs and the device bar carry them)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QDialog

from razertux.config.model import NAGA_MODEL
from razertux.devices.db import DeviceInfo, load_devices
from razertux.gui.dbus_client import DaemonError
from razertux.gui.device_bar import tab_keys
from razertux.gui.learn_dialog import LearnDialog
from razertux.hw.buttons import ButtonId


def device_info(model: str) -> DeviceInfo:
    db = load_devices()
    return db.get(model) or db[NAGA_MODEL]


class DeviceActions:
    """Needs from the window: store, client, settings, model, profiles, info, header,
    device_bar, tabs, tab_pages, the tab widgets, select_profile(), reload_profile_list(),
    _resolve_unsaved(), _on_status(), _refresh(), _warn()."""

    def _ask(self, call: Callable[..., Any], *args: Any, default: Any) -> Any:
        """A daemon call that may fail for this device (UnknownDevice, NotConnected)."""
        try:
            return call(*args)
        except DaemonError:
            return default

    def _devices(self) -> list[tuple[str, str, str, bool]]:
        known = load_devices()
        return [d for d in self._ask(self.client.devices, default=[]) if d[0] in known]

    def _initial_model(self) -> str:
        """The last used mouse if it is connected, else the first connected one, else the
        last used one (no mouse or no daemon: still editable)."""
        connected = [m for m, _n, _c, up in self._devices() if up]
        last = self.settings.last_model if self.settings.last_model in load_devices() else ""
        if last in connected or (last and not connected):
            return last
        return connected[0] if connected else (last or NAGA_MODEL)

    def switch_device(self, model: str) -> bool:
        if model == self.model:
            return True
        if not self._resolve_unsaved():
            self.device_bar.select(self.model)
            return False
        self.model = model
        self._enter_device()
        self._remember_model()
        return True

    def _enter_device(self) -> None:
        self.info = device_info(self.model)
        self.profiles = self.store.for_model(self.model)
        self._remap_enabled = self.profiles.load_device_settings().remap_enabled
        self.header.set_device_name(self.info.name)
        self.buttons_tab.set_device(self.info, self.profiles)
        for tab in (self.performance_tab, self.lighting_tab, self.power_tab, self.scroll_tab):
            tab.set_device(self.info)
        self._show_tabs()
        self._refresh_devices()
        self.reload_profile_list()
        self._active_id = (self._ask(self.client.active_profile, self.model, default="")
                           or self.store.load_settings().active_profiles.get(self.model, "default"))
        self.select_profile(self._active_id, force=True)
        self._on_status(self._ask(self.client.status, self.model, default={}))

    def _remember_model(self) -> None:
        self.settings.last_model = self.model
        try:
            on_disk = self.store.load_settings()
            on_disk.last_model = self.model
            self.store.save_settings(on_disk)
        except OSError:
            pass  # only a convenience for the next start

    def _show_tabs(self) -> None:
        """Tabs only for the functions this mouse has (TASTEN and MAKROS always)."""
        current = self.tabs.currentWidget()
        self.tabs.clear()
        for key in tab_keys(self.info):
            self.tabs.addTab(*self.tab_pages[key])
        self.tabs.setCurrentIndex(max(self.tabs.indexOf(current), 0))

    def _refresh_devices(self) -> None:
        devices = self._devices()
        if all(d[0] != self.model for d in devices):  # last used, not known to the daemon now
            devices.append((self.model, self.info.name, "", False))
        self.device_bar.set_devices(devices, self.model)

    def _show_device_status(self, status: dict) -> None:
        if status.get("name"):
            self.header.set_device_name(str(status["name"]))
        self.device_bar.set_status({"experimental": not self.info.verified, **(status or {})},
                                   self._remap_enabled)

    def set_remap_enabled(self, on: bool) -> None:
        """"Enable button assignment" of an experimental mouse: saved at once, like the plate."""
        ds = self.profiles.load_device_settings()
        ds.remap_enabled = on
        try:
            self.profiles.save_device_settings(ds)
        except OSError as e:
            self._warn(self.windowTitle(), str(e))
            return
        self._remap_enabled = on
        self.client.reload()

    # -- learning -------------------------------------------------------------------
    def learnable(self, button: ButtonId) -> bool:
        """"Learn button" for learn slots, experimental mice and the Naga's unchecked plates."""
        if button.startswith("learn_") or not self.info.verified:
            return True
        return self.model == NAGA_MODEL and self.buttons_tab.plate != 12 and button.startswith("side_")

    def learn_button(self, button: ButtonId) -> bool:
        dialog = LearnDialog(self.client, self.model, button, self.profiles, self)
        ok = self._run_learn(dialog) == QDialog.DialogCode.Accepted
        if ok:
            self.buttons_tab.refresh_device()
            self._refresh()
        return ok

    def _run_learn(self, dialog: LearnDialog) -> int:  # replaced in tests
        dialog.start()
        return dialog.exec()
