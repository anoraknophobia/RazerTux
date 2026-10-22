"""Hilfe → Über RazerTux: version, description, disclaimer, licence and credits."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from razertux import APP_NAME, PROJECT_URL, __version__
from razertux.gui.tray import app_icon

LICENSE = "GPL-2.0-or-later"


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Über RazerTux"))
        icon = app_icon()
        self.setWindowIcon(icon)

        logo = QLabel()
        logo.setPixmap(icon.pixmap(96, 96))
        logo.setAlignment(Qt.AlignmentFlag.AlignTop)
        name = QLabel(f"<b style='font-size: 18pt'>{APP_NAME}</b>")
        version = QLabel(self.tr("Version %1").replace("%1", __version__))
        description = QLabel(self.tr(
            "Inoffizieller Ersatz für Razer Synapse unter Linux: Tastenbelegung, Makros, "
            "DPI, Beleuchtung und Energie für die Razer Naga V2 Pro."))
        disclaimer = QLabel(self.tr("Kein offizielles Razer-Produkt, nicht mit Razer Inc. verbunden."))
        disclaimer.setObjectName("aboutDisclaimer")
        licence = QLabel(self.tr("Lizenz: %1").replace("%1", LICENSE))
        credit = QLabel(self.tr("Protokollwissen aus OpenRazer (GPL-2.0)"))
        self.url_label: QLabel | None = None
        if PROJECT_URL:
            self.url_label = QLabel(f'<a href="{PROJECT_URL}">{PROJECT_URL}</a>')
            self.url_label.setOpenExternalLinks(True)
            self.url_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        for label in (description, disclaimer):
            label.setWordWrap(True)

        text = QVBoxLayout()
        text.setSpacing(6)
        for w in (name, version, description, disclaimer, licence, credit, self.url_label):
            if w is not None:
                text.addWidget(w)
        text.addStretch(1)
        row = QHBoxLayout()
        row.setSpacing(16)
        row.addWidget(logo)
        row.addLayout(text, 1)

        buttons = QDialogButtonBox()
        self.close_button = buttons.addButton(self.tr("Schließen"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 12)
        layout.addLayout(row)
        layout.addWidget(buttons)
        self.setMinimumWidth(460)
