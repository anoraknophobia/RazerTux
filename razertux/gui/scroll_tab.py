"""Tab "Mausrad": the firmware's six scroll-wheel stages (Synapse style).

The active stage comes from the daemon status ("scroll_stage", reported by the
mouse when the front top button cycles it; -1 = unknown). Stage names are user
given and global (Settings.scroll_stage_names); the tab edits the Settings it
was given in place and emits `changed`. Tension/steps cannot be read or set on
Linux, the Synapse presets are shown for reference only."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from razertux.config.model import SCROLL_STAGES, Settings
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
        self.title = QLabel(self.tr("Stufe %1").replace("%1", str(slot)))
        self.title.setObjectName("stageSlot")
        self.title.setMinimumWidth(64)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(self.tr("Namen vergeben …"))
        self.name_edit.setMaxLength(40)
        self.marker = QLabel("● " + self.tr("aktiv"))
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

        stages = Card(self.tr("Mausrad-Stufen"))
        stages.add(description(self.tr(
            "Die Maus hat sechs Mausrad-Stufen. Die Namen folgen der Reihenfolge in Synapse "
            "(Stufe 1 = Ultra-fine … Stufe 6 = Custom); du kannst sie beliebig umbenennen.")))
        self.rows: list[StageRow] = []
        for slot in range(1, SCROLL_STAGES + 1):
            row = StageRow(slot)
            row.name_edit.textEdited.connect(lambda text, s=slot: self._rename(s, text))
            self.rows.append(row)
            stages.add(row)
        stages.body.addStretch(1)

        active = Card(self.tr("Aktive Stufe"))
        self.active_name = QLabel()
        self.active_name.setObjectName("activeName")
        self.active_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = self.active_name.font()
        font.setPointSize(max(font.pointSize(), 9) * 2)
        self.active_name.setFont(font)
        self.active_slot = QLabel()
        self.active_slot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        tension, self.tension_value = _tile(self.tr("Widerstand (Synapse)"))
        steps, self.steps_value = _tile(self.tr("Rasten (Synapse)"))
        tiles = QHBoxLayout()
        tiles.addWidget(tension)
        tiles.addWidget(steps)
        self.hint = description(self.tr(
            "Umschalten mit der oberen Taste vorne (wenn „Obere Tasten frei belegen“ aus ist). "
            "Widerstand und Rasten der Stufen lassen sich derzeit nur in Razer Synapse unter "
            "Windows ändern."))
        for item in (self.active_name, self.active_slot, tiles, self.hint):
            active.add(item)

        ref = Card(self.tr("Referenz (Synapse)"))
        self.reference = QTableWidget(len(SYNAPSE_PRESETS), 3)
        self.reference.setHorizontalHeaderLabels(
            [self.tr("Stufe"), self.tr("Widerstand"), self.tr("Rasten")])
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

        grid = QGridLayout(self)
        grid.setContentsMargins(16, 16, 16, 16)
        grid.setSpacing(16)
        grid.addWidget(stages, 0, 0, 2, 1)
        grid.addWidget(active, 0, 1, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(ref, 1, 1, Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        grid.setRowStretch(1, 1)
        self._show_active()

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
        return names.get(slot) or self.tr("Stufe %1").replace("%1", str(slot))

    def _show_active(self) -> None:
        if self._active < 0:
            self.active_name.setText(self.tr("unbekannt"))
            self.active_slot.setText(self.tr("Noch keine Meldung der Maus – einmal umschalten."))
            preset = None
        else:
            name = self._name(self._active)
            self.active_name.setText(name)
            self.active_slot.setText(self.tr("Stufe %1").replace("%1", str(self._active)))
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
