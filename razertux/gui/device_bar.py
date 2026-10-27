"""Device selector below the header: one pill per mouse the daemon knows (like
Synapse's device tabs) and, for an experimental (unverified) mouse, the banner
"Experimentell – ungetestet" with "Diagnose kopieren" and the switch for button
remapping (DeviceSettings.remap_enabled)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from razertux.config.model import SCROLL_FEATURES
from razertux.devices.db import DeviceInfo
from razertux.gui.dbus_client import DaemonClient, DaemonError
from razertux.gui.header import short_name
from razertux.gui.widgets import PillToggle, ToggleSwitch

NAGA_MODEL = "naga_v2_pro"
BANNER_COLOUR = "#8a5a00"



def tab_keys(info: DeviceInfo) -> list[str]:
    """Tabs of the main window for a mouse, in order: only functions it has."""
    keys = ["buttons"]
    if ("dpi" in info.features and info.dpi_max > 0) or "poll_rate" in info.features:
        keys.append("performance")
    if info.model == NAGA_MODEL or info.features & SCROLL_FEATURES:  # wheel stages / scroll options
        keys.append("scroll")
    if info.zones:
        keys.append("lighting")
    if "battery" in info.features:
        keys.append("power")
    keys.append("macros")
    return keys


def make_banner(text: str, colour: str = "#b3261e") -> QFrame:
    """Coloured message strip of the main window (hidden until needed)."""
    frame = QFrame()
    frame.setStyleSheet(f"QFrame {{ background: {colour}; }} QLabel {{ color: white; }}")
    row = QHBoxLayout(frame)
    frame.label = QLabel(text)  # type: ignore[attr-defined]
    frame.label.setWordWrap(True)  # type: ignore[attr-defined]
    row.addWidget(frame.label, 1)  # type: ignore[attr-defined]
    frame.hide()
    return frame


class DeviceBar(QWidget):
    deviceChanged = Signal(str)  # model (also when a keyboard was shown and the mouse is chosen)
    keyboardChosen = Signal(object)  # FoundDevice of a connected Razer keyboard (untested)
    remapToggled = Signal(bool)  # "Enable button assignment"

    def __init__(self, client: DaemonClient, parent: QWidget | None = None):
        super().__init__(parent)
        self.client = client
        self.model = ""
        self.pills: dict[str, QPushButton] = {}
        self.keyboard_pills: dict[str, QPushButton] = {}  # "kbd:<pid>:<bus>"
        self.keyboard_shown = False
        self._mice: list[tuple[str, str, str, bool]] = []
        self._keyboards: list = []
        self._pill_box = QHBoxLayout()
        self._pill_box.setContentsMargins(16, 6, 16, 2)
        self._pill = PillToggle()
        self._pill_box.addStretch(1)
        self._pill_box.addWidget(self._pill)
        self._pill_box.addStretch(1)

        self.banner = QFrame()
        self.banner.setStyleSheet(f"QFrame {{ background: {BANNER_COLOUR}; }} QLabel {{ color: white; }}")
        self.banner_title = QLabel(self.tr("Experimental – untested"))
        font = self.banner_title.font()
        font.setBold(True)
        self.banner_title.setFont(font)
        self.banner_text = QLabel(self.tr(
            "This mouse has not been tested with RazerTux yet. Features may be missing or wrong "
            "– a diagnostic report helps to support it."))
        self.banner_text.setWordWrap(True)
        self.readonly_label = QLabel(self.tr(
            "Read-only: the mouse did not answer the probe commands as expected."))
        self.readonly_label.setWordWrap(True)
        self.diag_btn = QPushButton(self.tr("Copy diagnostics"))
        self.diag_btn.setToolTip(self.tr("Copy the diagnostic text to the clipboard"))
        self.diag_btn.clicked.connect(lambda _=False: self.copy_diagnostics())
        self.remap_toggle = ToggleSwitch(self.tr("Enable button assignment"))
        self.remap_toggle.setToolTip(self.tr(
            "On: RazerTux takes over the buttons of this mouse (assignments and macros)."))
        self.remap_toggle.toggled.connect(self.remapToggled)
        texts = QVBoxLayout()
        for w in (self.banner_title, self.banner_text, self.readonly_label):
            texts.addWidget(w)
        row = QHBoxLayout(self.banner)
        row.setContentsMargins(16, 8, 16, 8)
        row.addLayout(texts, 1)
        row.addWidget(self.remap_toggle, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.diag_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.banner.hide()
        self.readonly_label.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(self._pill_box)
        lay.addWidget(self.banner)

    # -- devices -------------------------------------------------------------------
    def set_devices(self, devices: list[tuple[str, str, str, bool]], current: str) -> None:
        """(model, name, connection, connected) of every mouse; `current` is checked."""
        self._mice = list(devices)
        self.model = current
        self._rebuild()

    def set_keyboards(self, keyboards: list) -> None:
        """Connected Razer keyboards (FoundDevice), shown after the mice."""
        from razertux.gui.keyboard_view import keyboard_name
        keys = [(f"kbd:{k.pid:04x}:{k.bus}", keyboard_name(k)) for k in keyboards]
        if keys == [(key, n) for key, n in self._keyboard_keys()]:
            return
        self._keyboards = list(keyboards)
        self._rebuild()

    def _keyboard_keys(self) -> list[tuple[str, str]]:
        from razertux.gui.keyboard_view import keyboard_name
        return [(f"kbd:{k.pid:04x}:{k.bus}", keyboard_name(k)) for k in self._keyboards]

    def _rebuild(self) -> None:
        shown_key = next((k for k, b in self.keyboard_pills.items() if b.isChecked()), None)
        for b in [*self.pills.values(), *self.keyboard_pills.values()]:
            self._pill.group.removeButton(b)
            b.setParent(None)
            b.deleteLater()
        self.pills, self.keyboard_pills = {}, {}
        for model, name, _connection, connected in self._mice:
            b = self._pill.add(short_name(name))
            b.setToolTip(name if connected else self.tr("%1 (not connected)").replace("%1", name))
            b.clicked.connect(lambda _=False, m=model: self._clicked(m))
            self.pills[model] = b
        for dev, (key, name) in zip(self._keyboards, self._keyboard_keys()):
            b = self._pill.add(short_name(name))
            b.setToolTip(self.tr("%1 (keyboard, untested)").replace("%1", name))
            b.clicked.connect(lambda _=False, d=dev: self._keyboard_clicked(d))
            self.keyboard_pills[key] = b
        self._pill.setVisible(bool(self.pills or self.keyboard_pills))
        if self.keyboard_shown and shown_key in self.keyboard_pills:
            self.keyboard_pills[shown_key].setChecked(True)
        elif self.keyboard_shown:  # the shown keyboard was unplugged: back to the mouse
            self.keyboard_shown = False
            self.select(self.model)
            self.deviceChanged.emit(self.model)
        else:
            self.select(self.model)

    def select(self, model: str) -> None:
        """Check the pill of `model` (no signal)."""
        self.model = model
        self.keyboard_shown = False
        if model in self.pills:
            self.pills[model].setChecked(True)

    def _clicked(self, model: str) -> None:
        if model != self.model or self.keyboard_shown:
            self.deviceChanged.emit(model)  # the window calls select() when it switched

    def _keyboard_clicked(self, dev) -> None:
        self.keyboard_shown = True
        self.banner.hide()
        self.keyboardChosen.emit(dev)

    # -- banner --------------------------------------------------------------------
    def set_status(self, status: dict, remap_enabled: bool) -> None:
        experimental = bool(status.get("experimental"))
        self.banner.setVisible(experimental)
        self.readonly_label.setVisible(experimental and bool(status.get("connected"))
                                       and status.get("writable") is False)
        self.remap_toggle.blockSignals(True)
        self.remap_toggle.setChecked(remap_enabled)
        self.remap_toggle.blockSignals(False)

    def copy_diagnostics(self) -> None:
        try:
            text = self.client.diagnostics(self.model)
        except DaemonError as e:
            text = str(e)
        if not text:
            text = self.tr("No diagnostics available (service not running or mouse not probed).")
        QGuiApplication.clipboard().setText(text)
