"""Recorder wizard pages (except the button recording, see ``recorder_buttons``). Every page has
``title``, ``changed`` (navigation state changed), ``can_next()``, ``enter()`` and ``leave()``."""
from __future__ import annotations

import json

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QListWidget,
    QPlainTextEdit, QPushButton, QRadioButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from razertux.gui.layouts import by_name
from razertux.gui.recorder_buttons import LaserPicture
from razertux.gui.recorder_session import device_status
from razertux.recorder.recording import CHOICES, TEMPLATE_CHOICES
from razertux.recorder.report import Report, sanitize, summary_text

_STATUS = {"known": QT_TRANSLATE_NOOP("ChoosePage", "Known"),
           "experimental": QT_TRANSLATE_NOOP("ChoosePage", "Experimental"),
           "unknown": QT_TRANSLATE_NOOP("ChoosePage", "Unknown")}
KIND_ANSWERS = {"mouse": QT_TRANSLATE_NOOP("KindPage", "Mouse"),
                "keyboard": QT_TRANSLATE_NOOP("KindPage", "Keyboard"),
                "keypad": QT_TRANSLATE_NOOP("KindPage", "Keypad"),
                "headset": QT_TRANSLATE_NOOP("KindPage", "Headset"),
                "other": QT_TRANSLATE_NOOP("KindPage", "Other"),
                "unknown": QT_TRANSLATE_NOOP("KindPage", "I don't know")}
TEMPLATE_NAMES = {"ergo": QT_TRANSLATE_NOOP("TemplatePage", "Ergonomic (right hand)"),
                  "ambi": QT_TRANSLATE_NOOP("TemplatePage", "Symmetric"),
                  "compact": QT_TRANSLATE_NOOP("TemplatePage", "Compact"),
                  "vertical": QT_TRANSLATE_NOOP("TemplatePage", "Vertical"),
                  "mmo": QT_TRANSLATE_NOOP("TemplatePage", "MMO (many side buttons)"),
                  "generic": QT_TRANSLATE_NOOP("TemplatePage", "I don't know")}
LIGHT_ANSWERS = {"yes": QT_TRANSLATE_NOOP("LightPage", "It lit up"),
                 "no": QT_TRANSLATE_NOOP("LightPage", "It did not"),
                 "unsure": QT_TRANSLATE_NOOP("LightPage", "Not sure")}


def _label(text: str = "", name: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    lab.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)  # no extra height
    if name:
        lab.setObjectName(name)
    return lab


def _mono(read_only: bool = True) -> QPlainTextEdit:
    edit = QPlainTextEdit()
    edit.setReadOnly(read_only)
    font = QFont("monospace")
    font.setStyleHint(QFont.StyleHint.Monospace)
    edit.setFont(font)
    return edit


class Page(QWidget):
    changed = Signal()
    title = ""

    def __init__(self, session, parent: QWidget | None = None):
        super().__init__(parent)
        self.session = session
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(12)

    def can_next(self) -> bool:
        return True

    def enter(self) -> None:
        pass

    def leave(self) -> None:
        pass


class ChoosePage(Page):
    def __init__(self, session, list_devices, parent=None):
        super().__init__(session, parent)
        self.title = self.tr("Choose device")
        self._list_devices = list_devices
        self.devices: list = []
        self.lay.addWidget(_label(self.tr(
            "Pick the Razer device you want to record. The recording helps to add it to RazerTux: "
            "its buttons, a few read-only values and – if you agree – a short light test. "
            "Nothing is sent anywhere until you decide so at the end.")))
        self.list = QListWidget()
        self.list.currentRowChanged.connect(lambda _r: self.changed.emit())
        self.list.itemDoubleClicked.connect(lambda _i: self.changed.emit())
        self.refresh_btn = QPushButton(self.tr("Search again"))
        self.refresh_btn.clicked.connect(lambda _=False: self.refresh())
        self.empty = _label(self.tr("No Razer device found. Connect it and press “Search again”."),
                            "mutedText")
        row = QHBoxLayout()
        row.addWidget(self.empty, 1)
        row.addWidget(self.refresh_btn)
        self.lay.addWidget(self.list, 1)
        self.lay.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        self.devices = list(self._list_devices())
        self.list.clear()
        for dev in self.devices:
            status = QCoreApplication.translate("ChoosePage", _STATUS[device_status(dev.pid)])
            name = dev.product or self.tr("Razer device")
            self.list.addItem(f"{name}    {dev.vid:04x}:{dev.pid:04x}    {status}")
        if self.devices:
            self.list.setCurrentRow(0 if len(self.devices) == 1 else -1)
        self.empty.setVisible(not self.devices)
        self.changed.emit()

    def selected(self):
        row = self.list.currentRow()
        return self.devices[row] if 0 <= row < len(self.devices) else None

    def can_next(self) -> bool:
        return self.selected() is not None

    def enter(self) -> None:
        self.session.disconnect()


class PermissionPage(Page):
    connected = Signal()

    def __init__(self, session, availability, parent=None):
        super().__init__(session, parent)
        self.title = self.tr("Permissions")
        self._availability = availability
        self.lay.addWidget(_label(self.tr(
            "Linux protects input devices: normally only the system may read them directly. "
            "To record every button and read values from the device, RazerTux starts a small "
            "helper with your password (polkit). The helper can only read this one device and "
            "switch its light on and off for the light test; it never stores anything on the "
            "device and quits when the wizard closes.")))
        self.lay.addWidget(_label(self.tr(
            "Without the password the wizard records what your user may already read – often "
            "the device data and some buttons, but no values and no light test.")))
        self.password_btn = QPushButton(self.tr("Continue with password"))
        self.nopassword_btn = QPushButton(self.tr("Continue without password"))
        self.password_btn.clicked.connect(lambda _=False: self._go(True))
        self.nopassword_btn.clicked.connect(lambda _=False: self._go(False))
        row = QHBoxLayout()
        row.addWidget(self.password_btn)
        row.addWidget(self.nopassword_btn)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.reason = _label("", "mutedText")
        self.lay.addWidget(self.reason)
        self.lay.addStretch(1)

    def can_next(self) -> bool:
        return False  # the two buttons go on

    def enter(self) -> None:
        self.session.disconnect()
        ok, why = self._availability()
        self.password_btn.setEnabled(ok)
        self.nopassword_btn.setEnabled(True)
        self.reason.setText("" if ok else why)

    def _go(self, password: bool) -> None:
        self.password_btn.setEnabled(False)
        self.nopassword_btn.setEnabled(False)
        self.reason.setText(self.tr("Waiting for the password …") if password else self.tr("Opening the device …"))
        self.session.connect_backend(password, self._done, self._failed)

    def _done(self, notice: str) -> None:
        self.reason.setText(notice)
        self.connected.emit()

    def _failed(self, err: str) -> None:
        ok, _why = self._availability()
        self.password_btn.setEnabled(ok)
        self.nopassword_btn.setEnabled(True)
        self.reason.setText(self.tr("Could not open the device: %1").replace("%1", err) + "\n"
                            + self.tr("You can try again or continue without password."))


class DevicePage(Page):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        self.title = self.tr("Device data")
        self.lay.addWidget(_label(self.tr(
            "Collected from the system without sending anything to the device. This is part "
            "of the report; serial numbers, user names and paths are never included.")))
        self.mode = _label("", "mutedText")
        self.lay.addWidget(self.mode)
        self.text = _mono()
        self.lay.addWidget(self.text, 1)

    def enter(self) -> None:
        s = self.session
        r = s.report()
        r.buttons, r.reads, r.light = {}, {}, {}
        lines = [line for line in summary_text(r).splitlines() if not line.startswith("Standard keys")]
        self.text.setPlainText("\n".join(lines))
        ans = s.opened
        mode = self.tr("Mode: with password") if s.password else self.tr("Mode: without password")
        extra = []
        if not ans.get("listen"):
            extra.append(self.tr("No input node of this device is readable – buttons cannot be recorded."))
        if not ans.get("control"):
            extra.append(self.tr("No control access – values and the light test are skipped."))
        self.mode.setText("\n".join([mode, *extra]))


class KindPage(Page):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        self.title = self.tr("Kind of device")
        self.lay.addWidget(_label(self.tr("What kind of device is this?"), "cardTitle"))
        self.lay.addWidget(_label(self.tr(
            "RazerTux does not know this device yet. The answer decides how buttons are recorded: "
            "on keyboards and keypads ordinary typing keys are never stored.")))
        self.group = QButtonGroup(self)
        self.buttons: dict[str, QRadioButton] = {}
        for key, text in KIND_ANSWERS.items():
            rb = QRadioButton(QCoreApplication.translate("KindPage", text))
            rb.toggled.connect(lambda on, key=key: on and self._pick(key))
            self.group.addButton(rb)
            self.buttons[key] = rb
            self.lay.addWidget(rb)
        self.lay.addStretch(1)

    def _pick(self, key: str) -> None:
        self.session.kind_answer = key
        self.changed.emit()

    def can_next(self) -> bool:
        return self.session.kind_answer is not None

    def enter(self) -> None:
        if self.session.kind_answer is None:
            self.group.setExclusive(False)
            for rb in self.buttons.values():
                rb.setChecked(False)
            self.group.setExclusive(True)


class TemplatePage(Page):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        self.title = self.tr("Mouse shape")
        self.lay.addWidget(_label(self.tr("Which picture looks most like your mouse?"), "cardTitle"))
        self.lay.addWidget(_label(self.tr(
            "The wizard then asks for the buttons of that shape one by one; buttons it does not "
            "know come afterwards.")))
        grid = QGridLayout()
        self.group = QButtonGroup(self)
        self.choice_buttons: dict[str, QPushButton] = {}
        for i, choice in enumerate(CHOICES):
            card = QPushButton()
            card.setObjectName("templateCard")
            card.setCheckable(True)
            card.setMinimumSize(220, 210)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            inner = QVBoxLayout(card)
            pic = LaserPicture(by_name(TEMPLATE_CHOICES[choice]))
            pic.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            cap = QLabel(QCoreApplication.translate("TemplatePage", TEMPLATE_NAMES[choice]))
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cap.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            inner.addWidget(pic, 1)
            inner.addWidget(cap)
            card.toggled.connect(lambda on, choice=choice: on and self._pick(choice))
            self.group.addButton(card)
            self.choice_buttons[choice] = card
            grid.addWidget(card, i // 3, i % 3)
        self.lay.addLayout(grid, 1)

    def _pick(self, choice: str) -> None:
        self.session.template_choice = choice
        self.changed.emit()

    def can_next(self) -> bool:
        return self.session.template_choice is not None


class ReadsPage(Page):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        self.title = self.tr("Values")
        self.lay.addWidget(_label(self.tr(
            "Reading a few values from the device (firmware, DPI, battery …). These are read-only "
            "commands: nothing on the device is changed. The serial number is never read.")))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([self.tr("Value"), self.tr("Result"), self.tr("Status")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.lay.addWidget(self.table, 1)
        self.progress = _label("", "mutedText")
        self.lay.addWidget(self.progress)
        self._pending = 0

    def can_next(self) -> bool:
        return self._pending == 0

    def enter(self) -> None:
        names = list(self.session.opened.get("reads", []))
        if set(self.session.reads) == set(names):
            return  # already read
        self.session.reads = {}
        self.table.setRowCount(len(names))
        self._pending = len(names)
        for row, name in enumerate(names):
            self.table.setItem(row, 0, QTableWidgetItem(name))
            self.table.setItem(row, 1, QTableWidgetItem("…"))
            self.table.setItem(row, 2, QTableWidgetItem(""))
            self.session.call(lambda be, name=name: be.read(name),
                              lambda ans, row=row, name=name: self._got(row, name, ans),
                              lambda err, row=row, name=name: self._got(row, name, {"status": "failed"}))
        self._show()

    def _got(self, row: int, name: str, ans: dict) -> None:
        entry = {k: ans[k] for k in ("value", "raw", "status") if k in ans}
        self.session.reads[name] = entry
        value = entry.get("value", "")
        self.table.setItem(row, 1, QTableWidgetItem(json.dumps(value) if not isinstance(value, str) else value))
        self.table.setItem(row, 2, QTableWidgetItem(str(entry.get("status", ""))))
        self._pending -= 1
        self._show()

    def _show(self) -> None:
        self.progress.setText(self.tr("Reading … %1 left").replace("%1", str(self._pending))
                              if self._pending else self.tr("All values read."))
        self.changed.emit()


class _ZoneRow:
    def __init__(self, page: LightPage, zone: str, grid: QGridLayout, row: int):
        self.zone, self.lit = zone, False
        self.test_btn = QPushButton(QCoreApplication.translate("LightPage", "Light up green"))
        self.test_btn.clicked.connect(lambda _=False: page.test(self))
        grid.addWidget(QLabel(zone), row, 0)
        grid.addWidget(self.test_btn, row, 1)
        self.group = QButtonGroup(page)
        self.answers: dict[str, QRadioButton] = {}
        for col, (key, text) in enumerate(LIGHT_ANSWERS.items()):
            rb = QRadioButton(QCoreApplication.translate("LightPage", text))
            rb.setEnabled(False)
            rb.toggled.connect(lambda on, key=key: on and page.answer(self, key))
            self.group.addButton(rb)
            self.answers[key] = rb
            grid.addWidget(rb, row, 2 + col)


class LightPage(Page):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        self.title = self.tr("Light test")
        self.lay.addWidget(_label(self.tr(
            "Optional: each lighting zone is switched to green for a moment and then off, with the "
            "“do not store” flag – nothing is saved on the device. After unplugging and plugging "
            "the device in again its previous lighting is back.")))
        self.consent = QCheckBox(self.tr("Yes, run the light test on this device"))
        self.consent.toggled.connect(lambda _on: self._show())
        self.lay.addWidget(self.consent)
        self.grid = QGridLayout()
        self.grid.setColumnStretch(5, 1)
        self.grid.setColumnMinimumWidth(0, 120)
        self.lay.addLayout(self.grid)
        self.lay.addStretch(1)
        self.rows: dict[str, _ZoneRow] = {}
        self._zones: list[str] = []

    def enter(self) -> None:
        zones = list(self.session.opened.get("light_zones", []))
        if zones != self._zones or not self.session.light:
            self._zones = zones
            while self.grid.count():
                w = self.grid.takeAt(0).widget()
                if w is not None:
                    w.deleteLater()
            self.rows = {z: _ZoneRow(self, z, self.grid, i) for i, z in enumerate(zones)}
            self.session.light = {}
        self._show()

    def test(self, row: _ZoneRow) -> None:
        row.lit = True
        for rb in row.answers.values():
            rb.setEnabled(True)
        self.session.call(lambda be: be.light(row.zone, "green"))

    def answer(self, row: _ZoneRow, key: str) -> None:
        if isinstance(self.session.light, dict):
            self.session.light[row.zone] = key
        self._off(row)

    def _off(self, row: _ZoneRow) -> None:
        if row.lit:
            row.lit = False
            self.session.call(lambda be: be.light(row.zone, "off"))

    def leave(self) -> None:
        for row in self.rows.values():
            self._off(row)
        if not self.consent.isChecked():
            self.session.light = "declined"
        elif not isinstance(self.session.light, dict):
            self.session.light = {}

    def _show(self) -> None:
        on = self.consent.isChecked()
        if on and not isinstance(self.session.light, dict):
            self.session.light = {}
        for row in self.rows.values():
            row.test_btn.setEnabled(on)
        self.changed.emit()


class ReviewPage(Page):
    def __init__(self, session, parent=None):
        super().__init__(session, parent)
        self.title = self.tr("Review")
        self.lay.addWidget(_label(self.tr(
            "This is exactly what will be sent. The report is in English so that everybody can "
            "read it. You can add a note below (in English if you can): the device name, what "
            "did not work, anything unusual.")))
        top = QHBoxLayout()
        self.summary_view = _mono()
        self.json_view = _mono()
        top.addWidget(self.summary_view, 1)
        top.addWidget(self.json_view, 1)
        self.lay.addLayout(top, 1)
        self.lay.addWidget(_label(self.tr("Your notes"), "cardTitle"))
        self.notes = _mono(read_only=False)
        self.notes.setFont(QFont())
        self.notes.setMaximumHeight(110)
        self.notes.setPlaceholderText(self.tr("e.g. Razer Basilisk V3 X HyperSpeed, the DPI button gave no signal"))
        self.notes.textChanged.connect(self._notes_changed)
        self.lay.addWidget(self.notes)

    def _notes_changed(self) -> None:
        self.session.notes = self.notes.toPlainText()
        self.refresh()

    def enter(self) -> None:
        if self.notes.toPlainText() != self.session.notes:
            self.notes.setPlainText(self.session.notes)
        self.refresh()

    def refresh(self) -> None:
        report: Report = self.session.report()
        self.summary_view.setPlainText(summary_text(report))
        self.json_view.setPlainText(json.dumps(sanitize(report), indent=2, sort_keys=True, ensure_ascii=False))


class SendPage(Page):
    def __init__(self, session, submit, save, parent=None):
        super().__init__(session, parent)
        self.title = self.tr("Send")
        self.lay.addWidget(_label(self.tr(
            "“Submit on GitHub” opens a prefilled issue in your browser; you need a GitHub account. "
            "Nothing is published before you press “Create” there. You can also save the report "
            "as a file and attach it to an issue yourself.")))
        self.submit_btn = QPushButton(self.tr("Submit on GitHub"))
        self.save_btn = QPushButton(self.tr("Save as file"))
        self.submit_btn.clicked.connect(lambda _=False: submit())
        self.save_btn.clicked.connect(lambda _=False: save())
        row = QHBoxLayout()
        row.addWidget(self.submit_btn)
        row.addWidget(self.save_btn)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.status = _label("")
        self.lay.addWidget(self.status)
        self.lay.addWidget(_label(self.tr("Thank you for helping RazerTux support more devices!"), "mutedText"))
        self.lay.addStretch(1)

    def can_next(self) -> bool:
        return False
