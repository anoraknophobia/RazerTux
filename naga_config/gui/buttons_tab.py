"""Tab "Tasten": layer switch (Standard / Hypershift) above the mouse view."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from naga_config.gui.mouse_view import MouseView


class ButtonsTab(QWidget):
    bindingClicked = Signal(object)  # ButtonId
    layerChanged = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.layer_buttons: dict[str, QPushButton] = {}
        row = QHBoxLayout()
        group = QButtonGroup(self)
        for key, text in (("standard", self.tr("Standard")), ("hypershift", self.tr("Hypershift"))):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(key == "standard")
            b.setToolTip(self.tr("Hypershift-Ebene: gilt, solange die Hypershift-Taste gehalten wird")
                         if key == "hypershift" else self.tr("Normale Belegung"))
            b.clicked.connect(lambda _=False, k=key: self.layerChanged.emit(k))
            group.addButton(b)
            self.layer_buttons[key] = b
            row.addWidget(b)
        row.addStretch(1)
        self.mouse_view = MouseView()
        self.mouse_view.bindingClicked.connect(self.bindingClicked)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.addLayout(row)
        lay.addWidget(self.mouse_view, 1)

    def set_layer(self, layer: str) -> None:
        self.layer_buttons[layer].setChecked(True)
