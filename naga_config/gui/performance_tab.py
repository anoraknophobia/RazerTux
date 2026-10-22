"""Tab "Leistung": DPI (single value or 1-5 stages) and polling rate (Synapse style).

The tab edits the profile it was given in place and emits `changed` after each
user edit. Unchecked "DPI dieses Profils festlegen" / "Nicht ändern" keep the
profile's empty values, so the mouse keeps its own settings (R9)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from naga_config.config.model import DPI_MAX, DPI_MIN, MAX_STAGES, POLL_RATES, Profile
from naga_config.gui.widgets import Card, ToggleSwitch, description

DPI_STEP = 50
DEFAULT_DPI = 1800


def _round_dpi(v: int) -> int:
    return min(max(round(v / DPI_STEP) * DPI_STEP, DPI_MIN), DPI_MAX)


def _dpi_spin() -> QSpinBox:
    s = QSpinBox()
    s.setRange(DPI_MIN, DPI_MAX)
    s.setSingleStep(DPI_STEP)
    s.setKeyboardTracking(False)  # typed values arrive (and get rounded) on Enter/focus out
    s.setMinimumWidth(80)
    return s


class StageRow(QWidget):
    """One DPI stage: number (radio = active stage), slider, X and optional Y."""

    edited = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.radio = QRadioButton()
        self.radio.setToolTip(self.tr("Aktive Stufe"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(DPI_MIN // DPI_STEP, DPI_MAX // DPI_STEP)
        self.spin_x = _dpi_spin()
        self.y_label = QLabel(self.tr("Y"))
        self.spin_y = _dpi_spin()
        self.remove_btn = QPushButton("×")
        self.remove_btn.setToolTip(self.tr("Stufe entfernen"))
        self.remove_btn.setObjectName("iconButton")
        self.remove_btn.setFixedWidth(32)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for w, stretch in ((self.radio, 0), (self.slider, 1), (self.spin_x, 0),
                           (self.y_label, 0), (self.spin_y, 0), (self.remove_btn, 0)):
            lay.addWidget(w, stretch)
        self.slider.valueChanged.connect(lambda v: self.spin_x.setValue(v * DPI_STEP))
        self.spin_x.valueChanged.connect(self._x_changed)
        self.spin_y.valueChanged.connect(self._y_changed)

    def set_values(self, x: int, y: int) -> None:
        for w, v in ((self.spin_x, x), (self.spin_y, y), (self.slider, x // DPI_STEP)):
            w.blockSignals(True)
            w.setValue(v)
            w.blockSignals(False)

    def values(self) -> tuple[int, int]:
        return self.spin_x.value(), self.spin_y.value()

    def _x_changed(self, _v: int) -> None:
        v = self.spin_x.value()  # never the argument: rounding re-enters this slot
        if v % DPI_STEP:
            self.spin_x.setValue(_round_dpi(v))
            return
        self.slider.blockSignals(True)
        self.slider.setValue(v // DPI_STEP)
        self.slider.blockSignals(False)
        self.edited.emit()

    def _y_changed(self, _v: int) -> None:
        v = self.spin_y.value()
        if v % DPI_STEP:
            self.spin_y.setValue(_round_dpi(v))
            return
        self.edited.emit()


class PerformanceTab(QWidget):
    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.profile: Profile | None = None
        self._quiet = False
        self.rows: list[StageRow] = []

        sens = Card(self.tr("Empfindlichkeit"))
        sens.add(description(self.tr("Anzahl der Punkte pro Zoll (DPI), um die sich der Zeiger bewegt.")))
        self.set_dpi_box = QCheckBox(self.tr("DPI dieses Profils festlegen"))
        self.set_dpi_box.setToolTip(self.tr("Aus: Die Maus behält ihre eigenen DPI-Stufen (nicht ändern)."))
        sens.add(self.set_dpi_box)
        self.rows_box = QWidget()
        rows_lay = QVBoxLayout(self.rows_box)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        opts = QHBoxLayout()
        self.stages_toggle = ToggleSwitch(self.tr("Empfindlichkeitsstufen"))
        self.split_box = QCheckBox(self.tr("X/Y getrennt"))
        opts.addWidget(self.stages_toggle)
        opts.addSpacing(24)
        opts.addWidget(self.split_box)
        opts.addStretch(1)
        rows_lay.addLayout(opts)
        scale = QHBoxLayout()
        scale.addWidget(QLabel(self.tr("DPI")))
        scale.addStretch(1)
        scale.addWidget(QLabel(self.tr("%1 – %2, Schritte von %3")
                               .replace("%1", str(DPI_MIN)).replace("%2", str(DPI_MAX))
                               .replace("%3", str(DPI_STEP))))
        rows_lay.addLayout(scale)
        self.rows_layout = QVBoxLayout()
        rows_lay.addLayout(self.rows_layout)
        self.add_btn = QPushButton(self.tr("Stufe hinzufügen"))
        rows_lay.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignLeft)
        sens.add(self.rows_box)
        self.radio_group = QButtonGroup(self)

        poll = Card(self.tr("Abfragerate"))
        poll.add(description(self.tr("Wie oft pro Sekunde (Hz) die Maus Daten sendet.")))
        row = QHBoxLayout()
        self.poll_buttons: dict[int | None, QPushButton] = {}
        group = QButtonGroup(self)
        for rate in (None, *POLL_RATES):
            b = QPushButton(self.tr("Nicht ändern") if rate is None else str(rate))
            b.setCheckable(True)
            b.setMinimumWidth(72)
            group.addButton(b)
            row.addWidget(b)
            b.clicked.connect(lambda _=False, r=rate: self._set_poll(r))
            self.poll_buttons[rate] = b
        row.addStretch(1)
        poll.add(row)

        grid = QGridLayout(self)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setSpacing(16)
        grid.addWidget(sens, 0, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(poll, 0, 1, Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)

        self.set_dpi_box.toggled.connect(self._on_set_dpi)
        self.stages_toggle.toggled.connect(self._on_stages_toggle)
        self.split_box.toggled.connect(self._on_split)
        self.add_btn.clicked.connect(lambda _=False: self.add_stage())

    # -- loading ---------------------------------------------------------------
    def load(self, profile: Profile) -> None:
        self._quiet = True
        self.profile = profile
        stages = [tuple(s) for s in profile.dpi_stages] or [(DEFAULT_DPI, DEFAULT_DPI)]
        self.set_dpi_box.setChecked(bool(profile.dpi_stages))
        self.stages_toggle.setChecked(len(stages) > 1)
        self.split_box.setChecked(any(x != y for x, y in stages))
        self._rebuild(stages, min(max(profile.active_stage - 1, 0), len(stages) - 1))
        rate = profile.poll_rate if profile.poll_rate in POLL_RATES else None
        self.poll_buttons[rate].setChecked(True)
        self.rows_box.setEnabled(self.set_dpi_box.isChecked())
        self._quiet = False

    def _rebuild(self, stages: list[tuple[int, int]], active: int) -> None:
        quiet, self._quiet = self._quiet, True
        for r in self.rows:
            self.radio_group.removeButton(r.radio)
            r.setParent(None)
            r.deleteLater()
        self.rows = []
        for x, y in stages:
            row = StageRow()
            row.set_values(x, y)
            row.edited.connect(self._commit)
            row.radio.toggled.connect(lambda on: on and self._commit())
            row.remove_btn.clicked.connect(lambda _=False, r=row: self.remove_stage(self.rows.index(r)))
            self.radio_group.addButton(row.radio)
            self.rows_layout.addWidget(row)
            self.rows.append(row)
        self.rows[active].radio.setChecked(True)
        self._update_rows()
        self._quiet = quiet

    def _update_rows(self) -> None:
        multi, split = self.stages_toggle.isChecked(), self.split_box.isChecked()
        for i, r in enumerate(self.rows):
            r.radio.setText(str(i + 1))
            r.radio.setVisible(multi)
            r.remove_btn.setVisible(multi)
            r.remove_btn.setEnabled(len(self.rows) > 1)
            r.y_label.setVisible(split)
            r.spin_y.setVisible(split)
        self.add_btn.setVisible(multi)
        self.add_btn.setEnabled(len(self.rows) < MAX_STAGES)

    def _active(self) -> int:
        return next((i for i, r in enumerate(self.rows) if r.radio.isChecked()), 0)

    # -- edits -----------------------------------------------------------------
    def add_stage(self) -> bool:
        """Append a stage (double the last one); False at the 5-stage limit."""
        if len(self.rows) >= MAX_STAGES:
            return False
        stages = [r.values() for r in self.rows]
        x, y = stages[-1]
        stages.append((_round_dpi(x * 2), _round_dpi(y * 2)))
        self._rebuild(stages, self._active())
        self._commit()
        return True

    def remove_stage(self, index: int) -> None:
        if len(self.rows) <= 1:
            return
        stages = [r.values() for i, r in enumerate(self.rows) if i != index]
        active = self._active()
        if index < active:
            active -= 1
        self._rebuild(stages, min(active, len(stages) - 1))
        self._commit()

    def _on_set_dpi(self, on: bool) -> None:
        self.rows_box.setEnabled(on)
        self._commit()

    def _on_stages_toggle(self, on: bool) -> None:
        if self._quiet:
            return
        stages = [r.values() for r in self.rows]
        if not on:
            self._rebuild([stages[self._active()]], 0)
        elif len(stages) == 1:
            x, y = stages[0]
            self._rebuild([(x, y), (_round_dpi(x * 2), _round_dpi(y * 2))], 0)
        self._update_rows()
        self._commit()

    def _on_split(self, _on: bool) -> None:
        if self._quiet:
            return
        for r in self.rows:  # coupling (or starting to split) begins with Y = X
            r.set_values(r.spin_x.value(), r.spin_x.value())
        self._update_rows()
        self._commit()

    def _set_poll(self, rate: int | None) -> None:
        if self.profile is not None:
            self.profile.poll_rate = rate
            self.changed.emit()

    def _commit(self) -> None:
        if self._quiet or self.profile is None:
            return
        if not self.set_dpi_box.isChecked():
            self.profile.dpi_stages, self.profile.active_stage = [], 0
        else:
            split = self.split_box.isChecked()
            self.profile.dpi_stages = [(x, y if split else x) for x, y in (r.values() for r in self.rows)]
            self.profile.active_stage = self._active() + 1
        self.changed.emit()
