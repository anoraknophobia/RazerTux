"""Tab "Tasten": the mouse view with the "Standard | Hypershift" pill below it and
the switch for the top buttons (top right), like Synapse's Customize page."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from razertux.config.store import ModelStore
from razertux.devices.db import DeviceInfo, load_devices
from razertux.gui.layouts import Layout, layout_for
from razertux.gui.mouse_view import DEFAULT_MODEL, MouseView
from razertux.gui.widgets import PillToggle, ToggleSwitch
from razertux.hw.buttons import ButtonId


class ButtonsTab(QWidget):
    bindingClicked = Signal(object)  # ButtonId
    layerChanged = Signal(str)
    remapToggled = Signal(bool)  # "Obere Tasten frei belegen" (per profile)
    learnRequested = Signal(object)  # ButtonId: the free learn slot "Taste anlernen …" fills

    def __init__(self, model_store: ModelStore | None = None,
                 on_plate_saved: Callable[[], object] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.model_store = model_store
        self.on_plate_saved = on_plate_saved  # tells the daemon (Reload) after a saved plate change
        self.info = load_devices()[DEFAULT_MODEL]  # until the window picks a device (set_device)
        self.learned: set[ButtonId] = set()
        self.layout_full: Layout | None = None  # incl. the learn slots nobody taught yet
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
        self.plate_combo = QComboBox()
        for plate, text in ((12, self.tr("Seitenplatte: 12 Tasten")), (6, self.tr("Seitenplatte: 6 Tasten")),
                            (2, self.tr("Seitenplatte: 2 Tasten"))):
            self.plate_combo.addItem(text, plate)
        self.plate_hint = QLabel(self.tr("Belegung ungeprüft – falls eine Taste nicht stimmt: Taste anlernen"))
        self.plate_hint.setObjectName("hint")
        self.learn_btn = QPushButton(self.tr("Taste anlernen …"))
        self.learn_btn.setToolTip(self.tr("Eine weitere Taste der Maus anlernen und belegen"))
        self.learn_btn.clicked.connect(lambda _=False: self._learn_next())
        top = QHBoxLayout()
        top.addWidget(self.plate_combo)
        top.addWidget(self.plate_hint)
        top.addWidget(self.learn_btn)
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
        self.plate = 12
        self._load_device_settings()
        self._apply_plate()
        self.plate_combo.currentIndexChanged.connect(self._plate_chosen)

    def set_device(self, info: DeviceInfo, model_store: ModelStore | None) -> None:
        """Show another mouse: its drawing, its plate (Naga V2 Pro) and its learned keys."""
        self.info, self.model_store = info, model_store
        self.refresh_device()

    def refresh_device(self) -> None:
        """Re-read the device settings (after a key was learned)."""
        self._load_device_settings()
        self._apply_plate()

    def _load_device_settings(self) -> None:
        ds = self.model_store.load_device_settings() if self.model_store else None
        self.plate = ds.plate if ds else 12
        self.learned = set(ds.learned) if ds else set()
        self.plate_combo.blockSignals(True)
        self.plate_combo.setCurrentIndex(self.plate_combo.findData(self.plate))
        self.plate_combo.blockSignals(False)

    def _apply_plate(self) -> None:
        """Plate choice (Naga V2 Pro only): swap the drawing and the label list. Learn
        slots of a layout (generic mice) get a label only once a key was learned."""
        naga = self.info.model == DEFAULT_MODEL
        self.plate_combo.setVisible(naga)
        self.plate_hint.setVisible(naga and self.plate != 12)
        self.layout_full = layout_for(self.info, self.plate if naga else None)
        shown = {b: s for b, s in self.layout_full.buttons.items()
                 if not b.startswith("learn_") or b in self.learned}
        self.learn_btn.setVisible(self._free_slot() is not None)
        self.remap_toggle.setVisible(ButtonId.TOP_1 in shown)  # the Naga's firmware-owned top buttons
        self.mouse_view.set_layout(replace(self.layout_full, buttons=shown))

    def _free_slot(self) -> ButtonId | None:
        slots = [b for b in self.layout_full.buttons if b.startswith("learn_")] if self.layout_full else []
        return next((b for b in slots if b not in self.learned), None)

    def _learn_next(self) -> None:
        slot = self._free_slot()
        if slot is not None:
            self.learnRequested.emit(slot)

    def _plate_chosen(self) -> None:
        self.plate = self.plate_combo.currentData()
        if self.model_store is not None:  # saved at once; the profiles keep every binding
            ds = self.model_store.load_device_settings()
            ds.plate = self.plate
            self.model_store.save_device_settings(ds)
            if self.on_plate_saved is not None:
                self.on_plate_saved()
        self._apply_plate()

    def set_layer(self, layer: str) -> None:
        self.layer_buttons[layer].setChecked(True)

    def set_remap(self, on: bool) -> None:
        self.remap_toggle.blockSignals(True)
        self.remap_toggle.setChecked(on)
        self.remap_toggle.blockSignals(False)
