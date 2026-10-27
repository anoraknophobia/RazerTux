"""Window header like Synapse: profile combo and profile menu on the left, the
device name centred, DPI and battery on the right."""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMenu, QPushButton, QToolButton, QWidget,
)

from razertux.gui.labels import battery_text
from razertux.gui.widgets import BatteryIndicator


def short_name(name: str) -> str:
    """Product name without the brand ("Razer Naga V2 Pro" -> "Naga V2 Pro")."""
    return name.removeprefix("Razer ").strip() or name


class HeaderBar(QWidget):
    """slots: new, rename, duplicate, delete, export, import, activate, record."""

    def __init__(self, slots: dict[str, Callable[[], None]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("header")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(200)
        self.profile_combo.setToolTip(self.tr("Profile"))
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("dotsButton")
        self.menu_button.setText("•••")
        self.menu_button.setToolTip(self.tr("Profile actions"))
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.menu_button)
        for text, key in ((self.tr("New"), "new"), (self.tr("Rename"), "rename"),
                          (self.tr("Duplicate"), "duplicate"), (self.tr("Delete"), "delete"),
                          (self.tr("Export …"), "export"), (self.tr("Import …"), "import")):
            menu.addAction(text).triggered.connect(lambda _=False, s=slots[key]: s())
        self.menu_button.setMenu(menu)
        self.activate_btn = QPushButton(self.tr("Activate"))
        self.activate_btn.setToolTip(self.tr("Use this profile for the mouse"))
        self.activate_btn.clicked.connect(lambda _=False: slots["activate"]())
        self.title = QLabel(self)  # product name of the selected mouse, plain text, no logo
        self.title.setObjectName("deviceTitle")
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.dpi_label = QLabel()
        self.dpi_label.setObjectName("mutedText")
        self.battery_label = BatteryIndicator()
        self.add_device_btn = QPushButton("+  " + self.tr("Add unknown device"))
        self.add_device_btn.setObjectName("accentButton")  # filled Razer green (theme.py)
        self.add_device_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_device_btn.setToolTip(self.tr(
            "Record a Razer device RazerTux does not support yet and send the report on GitHub"))
        self.add_device_btn.clicked.connect(lambda _=False: slots["record"]())
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(8)
        row.addWidget(self.profile_combo)
        row.addWidget(self.menu_button)
        row.addWidget(self.activate_btn)
        row.addStretch(1)
        row.addWidget(self.dpi_label)
        row.addSpacing(16)
        row.addWidget(self.battery_label)
        row.addSpacing(16)
        row.addWidget(self.add_device_btn)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._place_title()

    def _place_title(self) -> None:
        hint = self.title.sizeHint()
        self.title.setGeometry((self.width() - hint.width()) // 2, (self.height() - hint.height()) // 2,
                               hint.width(), hint.height())

    def set_mouse_controls(self, visible: bool) -> None:
        """Profile, DPI and battery belong to the mouse: hidden while a keyboard is shown."""
        for w in (self.profile_combo, self.menu_button, self.activate_btn, self.dpi_label,
                  self.battery_label):
            w.setVisible(visible)

    def set_device_name(self, name: str) -> None:
        """"Razer Naga V2 Pro" is shown as "Naga V2 Pro" (from the status or the database)."""
        self.title.setText(short_name(name))
        self._place_title()

    def set_status(self, status: dict) -> None:
        """Battery (percentage + glyph, "Kabel" when wired) and current DPI."""
        connected = bool(status and status.get("connected"))
        level = int(status.get("battery", -1)) if connected else -1
        if not connected:
            self.battery_label.set_state("")
        elif status.get("connection") == "wired" or level < 0:
            self.battery_label.set_state(battery_text(status))
        else:
            self.battery_label.set_state(f"{level} %", level, bool(status.get("charging")))
        self.battery_label.setToolTip(battery_text(status) if connected else "")
        dpi = int(status.get("dpi", -1)) if connected else -1
        self.dpi_label.setText(self.tr("%1 DPI").replace("%1", str(dpi)) if dpi > 0 else "")
