"""Tab "Tasten": layer switch (Standard / Hypershift) above the mouse view."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from naga_config.gui.mouse_view import MouseView
from naga_config.gui.widgets import ToggleSwitch


class ButtonsTab(QWidget):
    bindingClicked = Signal(object)  # ButtonId
    layerChanged = Signal(str)
    remapToggled = Signal(bool)  # "Obere Tasten frei belegen" (per profile)

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
        self.remap_toggle = ToggleSwitch(self.tr("Obere Tasten frei belegen"))
        self.remap_toggle.setToolTip(self.tr(
            "Aus: Die Maus schaltet mit der oberen Taste vorne die Mausrad-Stufe und mit der "
            "hinteren die DPI-Stufe um. An: Beide Tasten lassen sich frei belegen."))
        self.remap_toggle.toggled.connect(self.remapToggled)
        row.addWidget(self.remap_toggle)
        self.mouse_view = MouseView()
        self.mouse_view.bindingClicked.connect(self.bindingClicked)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.addLayout(row)
        lay.addWidget(self.mouse_view, 1)

    def set_layer(self, layer: str) -> None:
        self.layer_buttons[layer].setChecked(True)

    def set_remap(self, on: bool) -> None:
        self.remap_toggle.blockSignals(True)
        self.remap_toggle.setChecked(on)
        self.remap_toggle.blockSignals(False)
