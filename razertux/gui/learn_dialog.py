""""Learn button": asks the daemon for the next key press the mouse's button
table does not know (StartLearning), stores it as `learned[button]` in the
device settings and tells the daemon to reload. Gives up after 15 s."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from razertux.config.store import ModelStore
from razertux.gui.dbus_client import DaemonClient, DaemonError
from razertux.gui.labels import button_name
from razertux.hw.button_maps import InputSignal
from razertux.hw.buttons import ButtonId

LEARN_TIMEOUT_MS = 15_000


class LearnDialog(QDialog):
    def __init__(self, client: DaemonClient, model: str, button: ButtonId, model_store: ModelStore,
                 parent: QWidget | None = None):
        super().__init__(parent)
        self.client, self.model, self.button, self.model_store = client, model, button, model_store
        self.learning = False
        self.signal: InputSignal | None = None
        self.setWindowTitle(self.tr("Learn button: %1").replace("%1", button_name(button)))
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setMinimumWidth(360)
        self.cancel_btn = QPushButton(self.tr("Cancel"))
        self.cancel_btn.clicked.connect(lambda _=False: self.reject())
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(LEARN_TIMEOUT_MS)
        self.timer.timeout.connect(self._timed_out)
        client.buttonLearned.connect(self._on_learned)
        self._listening = True
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.cancel_btn)
        lay = QVBoxLayout(self)
        lay.addWidget(self.message)
        lay.addLayout(row)

    def start(self) -> bool:
        """Ask the daemon to learn; False (and a message) if that is not possible now."""
        try:
            status = self.client.status(self.model)
            if not self.client.is_available:
                return self._fail(self.tr("The service is not running – learning needs the "
                                          "running service."))
            if not status.get("connected"):
                return self._fail(self.tr("The mouse is not connected – please connect it to learn."))
            if not self.client.start_learning(self.model):
                return self._fail(self.tr("The service is not running – learning needs the "
                                          "running service."))
        except DaemonError as e:
            return self._fail(self.tr("Learning not possible: %1").replace("%1", str(e)))
        self.learning = True
        self.message.setText(self.tr("Press the button on the mouse now …"))
        self.timer.start()
        return True

    def _fail(self, text: str) -> bool:
        self.message.setText(text)
        self.cancel_btn.setText(self.tr("Close"))
        return False

    def _stop(self) -> None:
        self.timer.stop()
        if self.learning:
            self.learning = False
            try:
                self.client.stop_learning(self.model)
            except DaemonError:
                pass  # the device went away meanwhile: nothing left to stop

    def _timed_out(self) -> None:
        self._stop()
        self._fail(self.tr("No button detected – please try again."))

    def _on_learned(self, model: str, source: str, ev_type: int, code: int) -> None:
        if not self.learning or model != self.model:
            return
        self.learning = False  # the daemon stops by itself after one press
        self.timer.stop()
        self.signal = InputSignal(source, ev_type, code)
        ds = self.model_store.load_device_settings()
        ds.learned = {b: s for b, s in ds.learned.items() if s != self.signal}  # one button per key
        ds.learned[self.button] = self.signal
        try:
            self.model_store.save_device_settings(ds)
        except OSError as e:
            self._fail(self.tr("Saving failed:") + f" {e}")
            return
        self.client.reload()
        self.accept()

    def reject(self) -> None:
        self._stop()
        super().reject()

    def done(self, r: int) -> None:
        self.timer.stop()
        if self._listening:
            self._listening = False
            self.client.buttonLearned.disconnect(self._on_learned)
        super().done(r)
