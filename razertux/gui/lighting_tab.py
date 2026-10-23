"""Tab "Beleuchtung": brightness and effect per zone (Naga V2 Pro: Logo, Daumenfeld;
other mice: their zones from the device database).

Zones the selected mouse lacks (the Naga V2 Pro has no scroll wheel LED) are kept
in the profile as they are but never shown. Unchecked "Beleuchtung dieses Profils festlegen" means
an empty `lighting` dict, i.e. the mouse keeps its own lighting."""
from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QT_TRANSLATE_NOOP, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QGridLayout, QHBoxLayout, QLabel,
    QPushButton, QSlider, QVBoxLayout, QWidget,
)

from razertux.config.model import LightingZone, Profile
from razertux.devices.db import DeviceInfo
from razertux.gui.widgets import Card, description

EFFECTS = [
    ("off", QT_TRANSLATE_NOOP("LightingTab", "Aus")),
    ("static", QT_TRANSLATE_NOOP("LightingTab", "Statisch")),
    ("breath", QT_TRANSLATE_NOOP("LightingTab", "Atmen")),
    ("spectrum", QT_TRANSLATE_NOOP("LightingTab", "Spektrum")),
    ("wave", QT_TRANSLATE_NOOP("LightingTab", "Welle")),
    ("reactive", QT_TRANSLATE_NOOP("LightingTab", "Reaktiv")),
]
COLOUR_EFFECTS = {"static", "breath", "reactive"}
ZONES = [
    ("all", QT_TRANSLATE_NOOP("LightingTab", "Alle Zonen")),
    ("logo", QT_TRANSLATE_NOOP("LightingTab", "Logo")),
    ("thumb", QT_TRANSLATE_NOOP("LightingTab", "Daumenfeld")),
    ("scroll", QT_TRANSLATE_NOOP("LightingTab", "Mausrad")),
    ("matrix", QT_TRANSLATE_NOOP("LightingTab", "Leuchtfeld")),
    ("backlight", QT_TRANSLATE_NOOP("LightingTab", "Hintergrund")),
    ("left", QT_TRANSLATE_NOOP("LightingTab", "Links")),
    ("right", QT_TRANSLATE_NOOP("LightingTab", "Rechts")),
]
DEVICE_ZONES = ("logo", "thumb")  # the Naga V2 Pro's own zone names
DEFAULT_ZONE = LightingZone("static", (68, 214, 44), 191)
_DB_EFFECT = {"off": "none"}  # LightingZone effect -> device database effect name


def zones_for(info: DeviceInfo) -> dict[str, tuple[str, ...]]:
    """Zone -> offered effects of a mouse (Naga V2 Pro: logo and thumb, every effect)."""
    if info.model == "naga_v2_pro":
        return {z: tuple(e for e, _ in EFFECTS) for z in DEVICE_ZONES}
    return {z: tuple(e for e, _ in EFFECTS if _DB_EFFECT.get(e, e) in fx) for z, fx in info.zones.items()}


def to_percent(raw: int) -> int:
    return round(raw * 100 / 255)


def to_raw(percent: int) -> int:
    return round(percent * 255 / 100)


class LightingTab(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.profile: Profile | None = None
        self.device_zones: tuple[str, ...] = DEVICE_ZONES
        self._zones: dict[str, LightingZone] = {z: DEFAULT_ZONE for z in DEVICE_ZONES}
        self._quiet = False

        self.enable_box = QCheckBox(self.tr("Beleuchtung dieses Profils festlegen"))
        self.enable_box.setToolTip(self.tr("Aus: Die Maus behält ihre Beleuchtung (nicht ändern)."))

        bright = Card(self.tr("Helligkeit"))
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness_value = QLabel()
        self.brightness_value.setObjectName("sliderValue")
        self.brightness_value.setMinimumWidth(48)
        row = QHBoxLayout()
        row.addWidget(self.brightness_value)
        row.addWidget(QLabel("0"))
        row.addWidget(self.brightness, 1)
        row.addWidget(QLabel("100"))
        bright.add(row)

        fx = Card(self.tr("Effekte"))
        fx.add(description(self.tr("Der Effekt wird beim Aktivieren des Profils gesetzt und "
                                   "beim Abstecken der Maus wieder vergessen.")))
        self.all_same = QCheckBox(self.tr("Alle Zonen gleich"))
        fx.add(self.all_same)
        self.grid = QGridLayout()
        self.zone_labels: dict[str, QLabel] = {}
        self.effect_combos: dict[str, QComboBox] = {}
        self.color_buttons: dict[str, QPushButton] = {}
        self.grid.setColumnStretch(3, 1)
        fx.add(self.grid)
        self.zone_effects: dict[str, tuple[str, ...]] = {}
        self._build_rows({z: tuple(e for e, _ in EFFECTS) for z in DEVICE_ZONES})
        self.cards = QWidget()
        cards = QGridLayout(self.cards)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(16)
        cards.addWidget(bright, 0, 0, Qt.AlignmentFlag.AlignTop)
        cards.addWidget(fx, 0, 1, Qt.AlignmentFlag.AlignTop)
        cards.setColumnStretch(0, 1)
        cards.setColumnStretch(1, 1)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addWidget(self.enable_box)
        lay.addWidget(self.cards)
        lay.addStretch(1)

        self.enable_box.toggled.connect(self._on_enable)
        self.all_same.toggled.connect(self._on_all_same)
        self.brightness.valueChanged.connect(self._on_brightness)

    # -- device ----------------------------------------------------------------
    def set_device(self, info: DeviceInfo) -> None:
        """Zones and effects of the selected mouse (from the device database)."""
        self._build_rows(zones_for(info))
        if self.profile is not None:
            self.load(self.profile)

    def _build_rows(self, zones: dict[str, tuple[str, ...]]) -> None:
        for widgets in (self.zone_labels, self.effect_combos, self.color_buttons):
            for w in widgets.values():
                w.setParent(None)
                w.deleteLater()
            widgets.clear()
        self.device_zones = tuple(zones)
        common = [e for e, _ in EFFECTS if all(e in fx for fx in zones.values())]
        self.zone_effects = {"all": tuple(common), **zones}
        self._zones = {z: DEFAULT_ZONE for z in self.device_zones}
        names = dict(ZONES)
        for i, (key, effects) in enumerate(self.zone_effects.items()):
            label = QLabel(self.tr(names[key]) if key in names else key)
            combo = QComboBox()
            for eff, eff_text in EFFECTS:
                if eff in effects:
                    combo.addItem(self.tr(eff_text), eff)
            colour = QPushButton()
            colour.setToolTip(self.tr("Farbe wählen"))
            colour.setFixedSize(40, 24)
            self.grid.addWidget(label, i, 0)
            self.grid.addWidget(combo, i, 1)
            self.grid.addWidget(colour, i, 2)
            combo.currentIndexChanged.connect(lambda _i, k=key: self._on_effect(k))
            colour.clicked.connect(lambda _=False, k=key: self._on_colour(k))
            self.zone_labels[key], self.effect_combos[key], self.color_buttons[key] = label, combo, colour
        self.all_same.setVisible(len(self.device_zones) > 1)

    # -- hooks (replaced in tests) ----------------------------------------------
    def _pick_color(self, current: QColor) -> QColor | None:
        c = QColorDialog.getColor(current, self, self.tr("Farbe wählen"))
        return c if c.isValid() else None

    # -- loading ---------------------------------------------------------------
    def load(self, profile: Profile) -> None:
        self._quiet = True
        self.profile = profile
        light = profile.lighting
        first = next((light[z] for z in self.device_zones if z in light), DEFAULT_ZONE)
        self._zones = {z: light.get(z, first) for z in self.device_zones}
        self.enable_box.setChecked(bool(light))
        self.all_same.setChecked(len(self._zones) > 1  # one zone: shown under its own name
                                 and len({(z.effect, z.color) for z in self._zones.values()}) == 1)
        self.brightness.setValue(to_percent(max(z.brightness for z in self._zones.values())))
        self._show()
        self._quiet = False

    def _show(self) -> None:
        """Push self._zones into the widgets (no commits)."""
        quiet, self._quiet = self._quiet, True
        same = self.all_same.isChecked()
        shown = {"all": self._zones[self.device_zones[0]], **self._zones}
        for key, zone in shown.items():
            visible = (key == "all") == same
            self.effect_combos[key].setCurrentIndex(self.effect_combos[key].findData(zone.effect))
            self.color_buttons[key].setStyleSheet(
                "QPushButton { background: rgb(%d, %d, %d); border: 1px solid #888; }" % zone.color)
            self.zone_labels[key].setVisible(visible)
            self.effect_combos[key].setVisible(visible)
            self.color_buttons[key].setVisible(visible and zone.effect in COLOUR_EFFECTS)
        self.brightness_value.setText(f"{self.brightness.value()} %")
        self.cards.setEnabled(self.enable_box.isChecked())
        self._quiet = quiet

    # -- edits -----------------------------------------------------------------
    def set_effect(self, key: str, effect: str) -> None:
        combo = self.effect_combos[key]
        combo.setCurrentIndex(combo.findData(effect))

    def _targets(self, key: str) -> tuple[str, ...]:
        return self.device_zones if key == "all" else (key,)

    def _on_effect(self, key: str) -> None:
        if self._quiet:
            return
        effect = self.effect_combos[key].currentData()
        for z in self._targets(key):
            self._zones[z] = replace(self._zones[z], effect=effect)
        self._commit()

    def _on_colour(self, key: str) -> None:
        r, g, b = self._zones[self.device_zones[0] if key == "all" else key].color
        c = self._pick_color(QColor(r, g, b))
        if c is None:
            return
        for z in self._targets(key):
            self._zones[z] = replace(self._zones[z], color=(c.red(), c.green(), c.blue()))
        self._commit()

    def _on_brightness(self, percent: int) -> None:
        if self._quiet:
            return
        for z in self.device_zones:
            self._zones[z] = replace(self._zones[z], brightness=to_raw(percent))
        self._commit()

    def _on_all_same(self, on: bool) -> None:
        if self._quiet:
            return
        if on:  # every zone takes the first zone's effect and colour
            logo = self._zones[self.device_zones[0]]
            for z in self.device_zones:
                self._zones[z] = replace(self._zones[z], effect=logo.effect, color=logo.color)
        self._commit()

    def _on_enable(self, _on: bool) -> None:
        self._commit()

    def _commit(self) -> None:
        if self._quiet or self.profile is None:
            return
        if self.enable_box.isChecked():  # zones this mouse lacks are kept as they are
            kept = {z: c for z, c in self.profile.lighting.items() if z not in self.device_zones}
            self.profile.lighting = {**kept, **self._zones}
        else:
            self.profile.lighting = {}
        self._show()
        self.changed.emit()
