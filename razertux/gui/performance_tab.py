"""Tab "Leistung": DPI (single value or 1-5 stages) and polling rate (Synapse style).

The tab edits the profile it was given in place and emits `changed` after each
user edit. Unchecked "DPI dieses Profils festlegen" / "Nicht ändern" keep the
profile's empty values, so the mouse keeps its own settings (R9)."""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QRadioButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from razertux.config.model import (
    DEFAULT_DPI_LIMIT, DPI_LIMIT_CHOICES, DPI_MAX, DPI_MIN, MAX_STAGES, POLL_RATES, Profile, Settings,
)
from razertux.devices.db import DeviceInfo
from razertux.gui.widgets import Card, ToggleSwitch, description

DPI_STEP = 50
DEFAULT_DPI = 1800


def _round_dpi(v: int) -> int:
    return min(max(math.floor(v / DPI_STEP + 0.5) * DPI_STEP, DPI_MIN), DPI_MAX)  # half up


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
        self.radio.setToolTip(self.tr("Active stage"))
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(DPI_MIN // DPI_STEP, DPI_MAX // DPI_STEP)
        self.spin_x = _dpi_spin()
        self.y_label = QLabel(self.tr("Y"))
        self.spin_y = _dpi_spin()
        self.remove_btn = QPushButton("×")
        self.remove_btn.setToolTip(self.tr("Remove stage"))
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

    def set_limit(self, limit: int) -> None:
        """Slider/spin maximum; never below the current values (no silent cuts)."""
        top = max(limit, self.spin_x.value(), self.spin_y.value())
        for w in (self.spin_x, self.spin_y):
            w.blockSignals(True)
            w.setMaximum(top)
            w.blockSignals(False)
        self.slider.blockSignals(True)
        self.slider.setMaximum(top // DPI_STEP)
        self.slider.blockSignals(False)

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
        # full stage list + active index while "Empfindlichkeitsstufen" is off
        self._stashed: tuple[list[tuple[int, int]], int] | None = None
        self.settings: Settings | None = None
        self._limit = DEFAULT_DPI_LIMIT
        self._dpi_max = DPI_MAX  # the selected mouse's own maximum (device database)

        self.sens_card = sens = Card(self.tr("Sensitivity"))
        sens.add(description(self.tr("Number of dots per inch (DPI) the cursor moves.")))
        self.set_dpi_box = QCheckBox(self.tr("Set DPI for this profile"))
        self.set_dpi_box.setToolTip(self.tr("Off: the mouse keeps its own sensitivity stages (no change)."))
        sens.add(self.set_dpi_box)
        self.rows_box = QWidget()
        rows_lay = QVBoxLayout(self.rows_box)
        rows_lay.setContentsMargins(0, 0, 0, 0)
        opts = QHBoxLayout()
        self.stages_toggle = ToggleSwitch(self.tr("Sensitivity Stages"))
        self.split_box = QCheckBox(self.tr("Enable X-Y sensitivity"))
        opts.addWidget(self.stages_toggle)
        opts.addSpacing(24)
        opts.addWidget(self.split_box)
        opts.addStretch(1)
        rows_lay.addLayout(opts)
        scale = QHBoxLayout()
        scale.addWidget(QLabel(self.tr("DPI")))
        scale.addStretch(1)
        self.scale_label = QLabel()
        scale.addWidget(self.scale_label)
        scale.addSpacing(16)
        scale.addWidget(QLabel(self.tr("Slider limit")))
        self.limit_combo = QComboBox()
        self.limit_combo.setToolTip(self.tr("Highest value of the DPI sliders. The mouse itself goes up to %1 DPI.")
                                    .replace("%1", str(DPI_MAX)))
        for v in DPI_LIMIT_CHOICES:
            self.limit_combo.addItem(str(v), v)
        self.limit_combo.setCurrentIndex(DPI_LIMIT_CHOICES.index(DEFAULT_DPI_LIMIT))
        scale.addWidget(self.limit_combo)
        self._update_scale_label()
        rows_lay.addLayout(scale)
        self.rows_layout = QVBoxLayout()
        rows_lay.addLayout(self.rows_layout)
        self.add_btn = QPushButton(self.tr("Add stage"))
        rows_lay.addWidget(self.add_btn, 0, Qt.AlignmentFlag.AlignLeft)
        sens.add(self.rows_box)
        self.radio_group = QButtonGroup(self)

        self.poll_card = poll = Card(self.tr("Polling Rate"))
        poll.add(description(self.tr("How often per second (Hz) the mouse sends data.")))
        row = QHBoxLayout()
        self.poll_buttons: dict[int | None, QPushButton] = {}
        group = QButtonGroup(self)
        for rate in (None, *POLL_RATES):
            b = QPushButton(self.tr("Don't change") if rate is None else str(rate))
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
        self.limit_combo.currentIndexChanged.connect(self._on_limit)

    # -- device --------------------------------------------------------------------
    def set_device(self, info: DeviceInfo) -> None:
        """DPI maximum and polling rates of the selected mouse; cards for what it lacks hide."""
        has_dpi = "dpi" in info.features and info.dpi_max > 0
        self._dpi_max = info.dpi_max if has_dpi else DPI_MAX
        self.sens_card.setVisible(has_dpi)
        self.poll_card.setVisible("poll_rate" in info.features)
        for rate, b in self.poll_buttons.items():
            b.setVisible(rate is None or rate in info.poll_rates)
        self.limit_combo.setToolTip(self.tr("Highest value of the DPI sliders. The mouse itself goes up to %1 DPI.")
                                    .replace("%1", str(self._dpi_max)))
        self._apply_limit(self.limit_combo.currentData())

    # -- DPI limit (global setting) ----------------------------------------------
    def load_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.limit_combo.blockSignals(True)
        self.limit_combo.setCurrentIndex(DPI_LIMIT_CHOICES.index(settings.dpi_limit))
        self.limit_combo.blockSignals(False)
        self._apply_limit(settings.dpi_limit)

    def _on_limit(self, _index: int) -> None:
        limit = self.limit_combo.currentData()
        self._apply_limit(limit)
        if self.settings is not None:
            self.settings.dpi_limit = limit
            self.changed.emit()

    def _apply_limit(self, limit: int) -> None:
        """The chosen slider limit, capped at what the mouse can do."""
        limit = min(limit, self._dpi_max)
        self._limit = limit
        for r in self.rows:
            r.set_limit(limit)
        self._update_scale_label()

    def _double(self, v: int) -> int:
        """Next stage's default: twice the value, at most the slider limit."""
        return max(min(_round_dpi(v * 2), self._limit), v)

    def _update_scale_label(self) -> None:
        self.scale_label.setText(self.tr("%1 – %2, steps of %3").replace("%1", str(DPI_MIN))
                                 .replace("%2", str(self._limit)).replace("%3", str(DPI_STEP)))

    # -- loading ---------------------------------------------------------------
    def load(self, profile: Profile) -> None:
        self._quiet = True
        self.profile = profile
        self._stashed = None
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
            row.set_limit(self._limit)
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
        stages.append((self._double(x), self._double(y)))
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
            self._stashed = (stages, self._active())
            self._rebuild([stages[self._active()]], 0)
        elif self._stashed is not None:  # restore the stages hidden by "off"
            full, active = self._stashed
            full = list(full)
            full[active] = stages[0]  # keep edits made to the single value
            self._stashed = None
            self._rebuild(full, active)
        elif len(stages) == 1:
            x, y = stages[0]
            self._rebuild([(x, y), (self._double(x), self._double(y))], 0)
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
