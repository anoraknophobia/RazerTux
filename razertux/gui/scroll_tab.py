"""Tab "Mausrad": the firmware's six scroll-wheel stages (Synapse style).

The active stage comes from the daemon status ("scroll_stage", reported by the
mouse when the front top button cycles it; -1 = unknown). Stage names are user
given and global (Settings.scroll_stage_names); the tab edits the Settings it
was given in place and emits `changed`. Tension/steps cannot be read or set on
Linux, the Synapse presets are shown for reference only."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from razertux.config.model import NAGA_MODEL, SCROLL_FEATURES, SCROLL_STAGES, Profile, Settings
from razertux.devices.db import DeviceInfo
from razertux.gui.widgets import Card, description

# Synapse presets: name, tension, steps (read off Synapse on Windows)
SYNAPSE_PRESETS = (("Ultra-fine", 65, 96), ("Standard", 60, 24), ("Distinct", 58, 15),
                   ("Adaptive", 75, 24), ("Smooth Scroll", 0, 32), ("Custom", 50, 90))


def _restyle(w: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property changed."""
    w.style().unpolish(w)
    w.style().polish(w)


class StageRow(QFrame):
    """One stage: "Stufe n", editable name, marker when active."""

    def __init__(self, slot: int, parent: QWidget | None = None):
        super().__init__(parent)
        self.slot = slot
        self.setObjectName("stageRow")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setProperty("active", False)
        self.title = QLabel(self.tr("Stage %1").replace("%1", str(slot)))
        self.title.setObjectName("stageSlot")
        self.title.setMinimumWidth(64)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(self.tr("Enter a name …"))
        self.name_edit.setMaxLength(40)
        self.marker = QLabel("● " + self.tr("active"))
        self.marker.setObjectName("stageMarker")
        self.marker.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        lay.addWidget(self.title)
        lay.addWidget(self.name_edit, 1)
        lay.addWidget(self.marker)

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

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings: Settings | None = None
        self._active = -1

        stages = Card(self.tr("Scroll wheel stages"))
        stages.add(description(self.tr(
            "The mouse has six scroll wheel stages. The names follow the order in Synapse "
            "(stage 1 = Ultra-fine … stage 6 = Custom); you can rename them as you like.")))
        self.rows: list[StageRow] = []
        for slot in range(1, SCROLL_STAGES + 1):
            row = StageRow(slot)
            row.name_edit.textEdited.connect(lambda text, s=slot: self._rename(s, text))
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
            "Switch with the front top button (when “Assign top buttons freely” is off). "
            "Tension and steps of the stages can currently only be changed in Razer Synapse on "
            "Windows."))
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

        self.naga_cards = QWidget()
        grid = QGridLayout(self.naga_cards)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(16)
        grid.addWidget(stages, 0, 0, 2, 1)
        grid.addWidget(active, 0, 1, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(ref, 1, 1, Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(16)
        outer.addWidget(self.options_card, 0, Qt.AlignmentFlag.AlignTop)
        outer.addWidget(self.naga_cards, 1)
        self._show_active()

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
        for row in self.rows:
            row.name_edit.setText(settings.scroll_stage_names.get(row.slot, ""))
        self._show_active()

    def set_status(self, status: dict) -> None:
        stage = int(status.get("scroll_stage", -1)) if status else -1
        self._active = stage if 1 <= stage <= SCROLL_STAGES else -1
        for row in self.rows:
            row.set_active(row.slot == self._active)
        self._show_active()

    def _name(self, slot: int) -> str:
        names = self.settings.scroll_stage_names if self.settings else {}
        return names.get(slot) or self.tr("Stage %1").replace("%1", str(slot))

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
        self.tension_value.setText(str(preset[1]) if preset else "–")
        self.steps_value.setText(str(preset[2]) if preset else "–")

    def _rename(self, slot: int, text: str) -> None:
        if self.settings is None:
            return
        name = text.strip()
        if name:
            self.settings.scroll_stage_names[slot] = name
        else:
            self.settings.scroll_stage_names.pop(slot, None)
        self._show_active()
        self.changed.emit()
