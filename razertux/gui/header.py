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

DEVICE_NAME = "Naga V2 Pro"  # product name as plain text, no logo


class HeaderBar(QWidget):
    """slots: new, rename, duplicate, delete, export, import, activate."""

    def __init__(self, slots: dict[str, Callable[[], None]], parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("header")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(200)
        self.profile_combo.setToolTip(self.tr("Profil"))
        self.menu_button = QToolButton()
        self.menu_button.setObjectName("dotsButton")
        self.menu_button.setText("•••")
        self.menu_button.setToolTip(self.tr("Profilaktionen"))
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.menu_button)
        for text, key in ((self.tr("Neu"), "new"), (self.tr("Umbenennen"), "rename"),
                          (self.tr("Duplizieren"), "duplicate"), (self.tr("Löschen"), "delete"),
                          (self.tr("Export …"), "export"), (self.tr("Import …"), "import")):
            menu.addAction(text).triggered.connect(lambda _=False, s=slots[key]: s())
        self.menu_button.setMenu(menu)
        self.activate_btn = QPushButton(self.tr("Aktivieren"))
        self.activate_btn.setToolTip(self.tr("Dieses Profil für die Maus verwenden"))
        self.activate_btn.clicked.connect(lambda _=False: slots["activate"]())
        self.title = QLabel(DEVICE_NAME, self)
        self.title.setObjectName("deviceTitle")
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.dpi_label = QLabel()
        self.dpi_label.setObjectName("mutedText")
        self.battery_label = BatteryIndicator()
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

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        hint = self.title.sizeHint()
        self.title.setGeometry((self.width() - hint.width()) // 2, (self.height() - hint.height()) // 2,
                               hint.width(), hint.height())

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
