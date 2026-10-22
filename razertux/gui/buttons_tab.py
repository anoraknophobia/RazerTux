"""Tab "Tasten": the mouse view with the "Standard | Hypershift" pill below it and
the switch for the top buttons (top right), like Synapse's Customize page."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from razertux.gui.mouse_view import MouseView
from razertux.gui.widgets import PillToggle, ToggleSwitch


class ButtonsTab(QWidget):
    bindingClicked = Signal(object)  # ButtonId
    layerChanged = Signal(str)
    remapToggled = Signal(bool)  # "Obere Tasten frei belegen" (per profile)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.layer_buttons: dict[str, QPushButton] = {}
        self.layer_pill = PillToggle()
        for key, text in (("standard", self.tr("Standard")), ("hypershift", self.tr("Hypershift"))):
            b = self.layer_pill.add(text)
            b.setChecked(key == "standard")
            b.setToolTip(self.tr("Hypershift-Ebene: gilt, solange die Hypershift-Taste gehalten wird")
                         if key == "hypershift" else self.tr("Normale Belegung"))
            b.clicked.connect(lambda _=False, k=key: self.layerChanged.emit(k))
            self.layer_buttons[key] = b
        self.remap_toggle = ToggleSwitch(self.tr("Obere Tasten frei belegen"))
        self.remap_toggle.setToolTip(self.tr(
            "Aus: Die Maus schaltet mit der oberen Taste vorne die Mausrad-Stufe und mit der "
            "hinteren die DPI-Stufe um. An: Beide Tasten lassen sich frei belegen."))
        self.remap_toggle.toggled.connect(self.remapToggled)
        top = QHBoxLayout()
        top.addStretch(1)
        top.addWidget(self.remap_toggle)
        pill_row = QHBoxLayout()
        pill_row.addStretch(1)
        pill_row.addWidget(self.layer_pill)
        pill_row.addStretch(1)
        self.mouse_view = MouseView()
        self.mouse_view.bindingClicked.connect(self.bindingClicked)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 16, 24, 16)
        lay.setSpacing(8)
        lay.addLayout(top)
        lay.addWidget(self.mouse_view, 1)
        lay.addLayout(pill_row)

    def set_layer(self, layer: str) -> None:
        self.layer_buttons[layer].setChecked(True)

    def set_remap(self, on: bool) -> None:
        self.remap_toggle.blockSignals(True)
        self.remap_toggle.setChecked(on)
        self.remap_toggle.blockSignals(False)
