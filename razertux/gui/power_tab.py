"""Tab "Energie": sleep timer, the mouse's low power mode and the battery warning.

These are global settings (not per profile). Unchecked idle/low power boxes keep
None in Settings, so the mouse keeps its own values."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QGridLayout, QHBoxLayout, QLabel, QSlider, QSpinBox, QWidget,
)

from razertux.config.model import (
    IDLE_MAX_S, IDLE_MIN_S, LOW_POWER_MAX, LOW_POWER_MIN, Settings,
)
from razertux.devices.db import DeviceInfo
from razertux.gui.widgets import Card, description

DEFAULT_IDLE_MIN = 5
DEFAULT_WARN = 15


def _slider(lo: int, hi: int) -> QSlider:
    s = QSlider(Qt.Orientation.Horizontal)
    s.setRange(lo, hi)
    return s


class PowerTab(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings: Settings | None = None
        self._quiet = False

        self.idle_card = idle = Card(self.tr("Energiesparen im Funkbetrieb"))
        self.idle_box = QCheckBox(self.tr("Ruhezustand nach Inaktivität (Minuten)"))
        self.idle_slider = _slider(IDLE_MIN_S // 60, IDLE_MAX_S // 60)
        self.idle_value = QLabel()
        idle.add(self.idle_box)
        idle.add(self._row(self.idle_slider, self.idle_value, "1", "15"))

        self.low_card = low = Card(self.tr("Energiesparmodus"))
        self.low_power_box = QCheckBox(self.tr("Im Funkbetrieb Energiesparmodus, wenn der Akku "
                                               "unter (%) liegt"))
        self.low_power_slider = _slider(LOW_POWER_MIN, LOW_POWER_MAX)
        self.low_power_value = QLabel()
        low.add(self.low_power_box)
        low.add(self._row(self.low_power_slider, self.low_power_value,
                          f"{LOW_POWER_MIN} %", f"{LOW_POWER_MAX} %"))

        self.warn_card = warn = Card(self.tr("Akku-Warnung"))
        warn.add(description(self.tr("Desktop-Benachrichtigung im Funkbetrieb, einmal je "
                                     "Entladung (erneut erst nach dem Laden).")))
        self.warn_box = QCheckBox(self.tr("Warnung bei Akku unter"))
        self.warn_spin = QSpinBox()
        self.warn_spin.setRange(5, 50)
        self.warn_spin.setSuffix(" %")
        self.warn_spin.setValue(DEFAULT_WARN)
        row = QHBoxLayout()
        row.addWidget(self.warn_box)
        row.addWidget(self.warn_spin)
        row.addStretch(1)
        warn.add(row)

        note = description(self.tr("Diese Einstellungen gelten für alle Profile. Nicht "
                                   "angehakt: Die Maus behält ihren eigenen Wert."))
        grid = QGridLayout(self)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setSpacing(16)
        grid.addWidget(idle, 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(low, 0, 1, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(warn, 1, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(note, 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(3, 1)

        for box in (self.idle_box, self.low_power_box, self.warn_box):
            box.toggled.connect(self._commit)
        for w in (self.idle_slider, self.low_power_slider, self.warn_spin):
            w.valueChanged.connect(self._commit)

    @staticmethod
    def _row(slider: QSlider, value: QLabel, lo: str, hi: str) -> QHBoxLayout:
        value.setObjectName("sliderValue")
        value.setMinimumWidth(56)
        row = QHBoxLayout()
        row.addWidget(value)
        row.addWidget(QLabel(lo))
        row.addWidget(slider, 1)
        row.addWidget(QLabel(hi))
        return row

    def set_device(self, info: DeviceInfo) -> None:
        """Sleep timer and low power mode are global and written only to verified mice
        (the Naga V2 Pro); an experimental battery mouse gets the desktop warning only."""
        own = info.verified and "battery" in info.features
        self.idle_card.setVisible(own and "idle_time" in info.features)
        self.low_card.setVisible(own and "low_battery_threshold" in info.features)

    def load(self, settings: Settings) -> None:
        self._quiet = True
        self.settings = settings
        self.idle_box.setChecked(settings.idle_time is not None)
        minutes = round((settings.idle_time or DEFAULT_IDLE_MIN * 60) / 60)
        self.idle_slider.setValue(min(max(minutes, 1), IDLE_MAX_S // 60))
        self.low_power_box.setChecked(settings.low_battery_threshold is not None)
        self.low_power_slider.setValue(settings.low_battery_threshold or LOW_POWER_MIN)
        self.warn_box.setChecked(settings.low_battery_notify > 0)
        self.warn_spin.setValue(settings.low_battery_notify or DEFAULT_WARN)
        self._show()
        self._quiet = False

    def _show(self) -> None:
        self.idle_value.setText(self.tr("%1 min").replace("%1", str(self.idle_slider.value())))
        self.low_power_value.setText(f"{self.low_power_slider.value()} %")
        self.idle_slider.setEnabled(self.idle_box.isChecked())
        self.low_power_slider.setEnabled(self.low_power_box.isChecked())
        self.warn_spin.setEnabled(self.warn_box.isChecked())

    def _commit(self, *_: object) -> None:
        self._show()
        if self._quiet or self.settings is None:
            return
        s = self.settings
        s.idle_time = self.idle_slider.value() * 60 if self.idle_box.isChecked() else None
        s.low_battery_threshold = (self.low_power_slider.value()
                                   if self.low_power_box.isChecked() else None)
        s.low_battery_notify = self.warn_spin.value() if self.warn_box.isChecked() else 0
        self.changed.emit()
