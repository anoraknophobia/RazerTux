"""Tab "Scroll wheel": the firmware's six scroll-wheel stages (Synapse style).

The active stage comes from the daemon status ("scroll_stage", reported by the
mouse when the front top button cycles it or the daemon reads it at connect;
-1 = unknown). The stage names are fixed: Synapse's list order (user-verified).
A click on a stage emits `stageRequested(slot)` (the window asks the daemon to
switch). The "Custom" card writes that stage's tension and steps to the mouse
(`customRequested`); the values are kept in Settings.custom_scroll. The
other presets are fixed in the mouse and shown for reference only."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from razertux.config.model import (
    NAGA_MODEL, SCROLL_FEATURES, SCROLL_STAGES, Profile, Settings, default_stage_names,
)
from razertux.devices.db import DeviceInfo
from razertux.gui.widgets import Card, description

# Synapse presets: name, tension, steps (read off Synapse on Windows)
SYNAPSE_PRESETS = (("Ultra-fine", 65, 96), ("Standard", 60, 24), ("Distinct", 58, 15),
                   ("Adaptive", 75, 24), ("Smooth Scroll", 0, 32), ("Custom", 50, 90))
STAGE_NAMES = default_stage_names()  # slot -> fixed name (Synapse order)
CUSTOM_SLOT = 6  # "Custom": tension/steps set in the card below


def _restyle(w: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    w.style().unpolish(w)
    w.style().polish(w)


class StageRow(QFrame):
    """One stage: "Stage n", its fixed name, marker when active; click to choose it."""

    clicked = Signal(int)

    def __init__(self, slot: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.slot = slot
        self.selectable = True
        self.setObjectName("stageRow")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setProperty("active", False)
        self.title = QLabel(self.tr("Stage %1").replace("%1", str(slot)))
        self.title.setObjectName("stageSlot")
        self.title.setMinimumWidth(64)
        self.name = QLabel(STAGE_NAMES[slot])
        self.marker = QLabel("● " + self.tr("active"))
        self.marker.setObjectName("stageMarker")
        self.marker.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.addWidget(self.title)
        lay.addWidget(self.name, 1)
        lay.addWidget(self.marker)
        if self.selectable:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self.setToolTip(self.tr("Click to switch the mouse to this stage"))

    def mousePressEvent(self, e) -> None:
        if self.selectable and e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.slot)
            e.accept()
            return
        super().mousePressEvent(e)

    def set_active(self, on: bool) -> None:
        if self.property("active") != on:
            self.setProperty("active", on)
            _restyle(self)
        self.marker.setVisible(on)


def _tile(caption: str) -> tuple[QFrame, QLabel]:
    frame = QFrame()
    frame.setObjectName("valueTile")
    frame.setFrameShape(QFrame.Shape.StyledPanel)
    value = QLabel("–")
    value.setObjectName("tileValue")
    value.setAlignment(Qt.AlignmentFlag.AlignCenter)
    cap = QLabel(caption)
    cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lay = QVBoxLayout(frame)
    lay.addWidget(value)
    lay.addWidget(cap)
    return frame, value


class ScrollTab(QWidget):
    changed = Signal()
    stageRequested = Signal(int)  # a stage row was clicked (1-6)
    customRequested = Signal(int, int)  # "Apply": write Custom (tension, steps) to the mouse

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings: Settings | None = None
        self._active = -1
        self._loading = False

        stages = Card(self.tr("Scroll wheel stages"))
        stages.add(description(self.tr(
            "The mouse has six scroll wheel stages, named as in Synapse. Click a stage to "
            "switch to it, or use the front top button. The values of “Custom” can be set "
            "below.")))
        self.rows: list[StageRow] = []
        for slot in range(1, SCROLL_STAGES + 1):
            row = StageRow(slot)
            row.clicked.connect(self.stageRequested)
            self.rows.append(row)
            stages.add(row)
        stages.body.addStretch(1)

        active = Card(self.tr("Active stage"))
        self.active_name = QLabel()
        self.active_name.setObjectName("activeName")
        self.active_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.active_name.font()
        font.setPointSize(max(font.pointSize(), 9) * 2)
        self.active_name.setFont(font)
        self.active_slot = QLabel()
        self.active_slot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tension, self.tension_value = _tile(self.tr("Tension (Synapse)"))
        steps, self.steps_value = _tile(self.tr("Steps (Synapse)"))
        tiles = QHBoxLayout()
        tiles.addWidget(tension)
        tiles.addWidget(steps)
        self.hint = description(self.tr(
            "Switch with a click on a stage or with the front top button. Stages 1–5 are fixed "
            "in the mouse (values as in Razer Synapse); “Custom” can be set below."))
        for item in (self.active_name, self.active_slot, tiles, self.hint):
            active.add(item)

        ref = Card(self.tr("Reference (Synapse)"))
        self.reference = QTableWidget(len(SYNAPSE_PRESETS), 3)
        self.reference.setHorizontalHeaderLabels(
            [self.tr("Stage"), self.tr("Tension"), self.tr("Steps")])
        for r, values in enumerate(SYNAPSE_PRESETS):
            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if c:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.reference.setItem(r, c, item)
        self.reference.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.reference.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.reference.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.reference.verticalHeader().hide()
        self.reference.setShowGrid(False)
        self.reference.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.reference.setFixedHeight(self.reference.horizontalHeader().sizeHint().height()
                                      + sum(self.reference.rowHeight(r)
                                            for r in range(len(SYNAPSE_PRESETS))) + 4)
        ref.add(self.reference)

        self.profile: Profile | None = None
        self.options_card = Card(self.tr("Scroll options"))
        self.options_card.add(description(self.tr(
            "Applies to the profile. “Don't change” leaves the mouse's setting as it is.")))
        self.mode_row, self.mode_buttons = self._option_row(
            self.tr("Mode"), "mode", ((0, self.tr("Notched")), (1, self.tr("Free-spinning"))))
        self.accel_row, self.accel_buttons = self._option_row(
            self.tr("Acceleration"), "acceleration", ((True, self.tr("On")), (False, self.tr("Off"))))
        self.reel_row, self.reel_buttons = self._option_row(
            self.tr("Smart Reel"), "smart_reel", ((True, self.tr("On")), (False, self.tr("Off"))))
        self.options_card.hide()

        self.custom_card = Card(self.tr("Custom stage"))
        self.custom_card.add(description(self.tr(
            "Sets tension and steps of the “Custom” stage in the mouse, the way Synapse does it.")))
        self.tension_slider, self.tension_label = self._slider(self.tr("Tension"), 0, 100)
        self.steps_slider, self.steps_label = self._slider(self.tr("Steps"), 8, 96)
        self.apply_custom = QPushButton(self.tr("Apply to the mouse"))
        self.apply_custom.clicked.connect(lambda _=False: self.customRequested.emit(
            self.tension_slider.value(), self.steps_slider.value()))
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.apply_custom)
        self.custom_card.add(row)

        self.naga_cards = QWidget()
        grid = QGridLayout(self.naga_cards)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)
        grid.addWidget(stages, 0, 0, 2, 1)
        grid.addWidget(active, 0, 1, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(ref, 1, 1, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.custom_card, 2, 0, 1, 2, Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(16)
        outer.addWidget(self.options_card, 0, Qt.AlignmentFlag.AlignTop)
        outer.addWidget(self.naga_cards, 1)
        self._show_active()

    # -- "Custom" stage values (Naga) --
    def _slider(self, caption: str, lo: int, hi: int) -> tuple[QSlider, QLabel]:
        label = QLabel(caption)
        label.setMinimumWidth(120)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        value = QLabel(str(lo))
        value.setMinimumWidth(32)
        slider.valueChanged.connect(lambda v: (value.setText(str(v)), self._custom_changed()))
        row = QHBoxLayout()
        row.addWidget(label)
        row.addWidget(slider, 1)
        row.addWidget(value)
        self.custom_card.add(row)
        return slider, value

    def _custom_changed(self) -> None:
        if self.settings is None or self._loading:
            return
        self.settings.custom_scroll = {"tension": self.tension_slider.value(),
                                       "steps": self.steps_slider.value()}
        self._show_active()
        self.changed.emit()

    # -- scroll options (mice with scroll_mode / scroll_acceleration / scroll_smart_reel) --
    def _option_row(self, caption: str, key: str, choices) -> tuple[QWidget, dict]:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        label = QLabel(caption)
        label.setMinimumWidth(120)
        lay.addWidget(label)
        group = QButtonGroup(row)
        buttons: dict = {}
        for value, text in ((None, self.tr("Don't change")), *choices):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setMinimumWidth(72)
            group.addButton(b)
            lay.addWidget(b)
            b.clicked.connect(lambda _=False, k=key, v=value: self._set_option(k, v))
            buttons[value] = b
        lay.addStretch(1)
        buttons[None].setChecked(True)
        self.options_card.add(row)
        return row, buttons

    def set_device(self, info: DeviceInfo) -> None:
        """Naga: stages and reference; other mice: the scroll options they have."""
        self.naga_cards.setVisible(info.model == NAGA_MODEL)
        for feature, row in (("scroll_mode", self.mode_row), ("scroll_acceleration", self.accel_row),
                             ("scroll_smart_reel", self.reel_row)):
            row.setVisible(feature in info.features)
        self.options_card.setVisible(bool(info.features & SCROLL_FEATURES))

    def load_profile(self, profile: Profile) -> None:
        self.profile = profile
        for key, buttons in (("mode", self.mode_buttons), ("acceleration", self.accel_buttons),
                             ("smart_reel", self.reel_buttons)):
            value = profile.scroll.get(key)
            buttons[value if value in buttons else None].setChecked(True)

    def _set_option(self, key: str, value) -> None:
        if self.profile is None:
            return
        if value is None:
            self.profile.scroll.pop(key, None)
        else:
            self.profile.scroll[key] = value
        self.changed.emit()

    def load(self, settings: Settings) -> None:
        self.settings = settings
        self._loading = True
        self.tension_slider.setValue(settings.custom_scroll["tension"])
        self.steps_slider.setValue(settings.custom_scroll["steps"])
        self._loading = False
        self._show_active()

    def set_status(self, status: dict) -> None:
        stage = int(status.get("scroll_stage", -1)) if status else -1
        self._active = stage if 1 <= stage <= SCROLL_STAGES else -1
        for row in self.rows:
            row.set_active(row.slot == self._active)
        self._show_active()

    def _name(self, slot: int) -> str:
        return STAGE_NAMES.get(slot) or self.tr("Stage %1").replace("%1", str(slot))

    def _show_active(self) -> None:
        if self._active < 0:
            self.active_name.setText(self.tr("unknown"))
            self.active_slot.setText(self.tr("No report from the mouse yet – switch once."))
            preset = None
        else:
            name = self._name(self._active)
            self.active_name.setText(name)
            self.active_slot.setText(self.tr("Stage %1").replace("%1", str(self._active)))
            preset = next((p for p in SYNAPSE_PRESETS if p[0].casefold() == name.casefold()), None)
            if self._active == CUSTOM_SLOT and self.settings is not None:  # Custom: our values
                preset = (name, self.settings.custom_scroll["tension"], self.settings.custom_scroll["steps"])
        self.tension_value.setText(str(preset[1]) if preset else "–")
        self.steps_value.setText(str(preset[2]) if preset else "–")
