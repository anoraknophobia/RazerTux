"""State of one device recording for the wizard: the chosen device, the backend (run on a
worker thread, one request after the other), the daemon pause and the collected report parts."""
from __future__ import annotations

import queue
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal

from razertux.devices import db
from razertux.gui.dbus_client import DaemonClient, DaemonError
from razertux.recorder.backend import Backend, HelperBackend, InProcessBackend
from razertux.recorder.discover import FoundDevice, report_device, report_interfaces
from razertux.recorder.recording import AUTO_STOP
from razertux.recorder.report import Report, system_section

LISTEN_SECONDS = int(AUTO_STOP)


def default_backend(password: bool) -> Backend:
    """Password mode: the pkexec record helper (started by the session); else in-process."""
    return HelperBackend() if password else InProcessBackend()


def device_status(pid: int) -> str:
    """"known" (verified in the database), "experimental" (in the database) or "unknown"."""
    info = db.by_pid_any(pid)
    if info is None:
        return "unknown"
    return "known" if info.verified else "experimental"


class SerialWorker(QObject):
    """Runs jobs one after the other on a daemon thread; done/failed callbacks run on the
    GUI thread (queued signal)."""

    _result = Signal(object, object)  # callback, argument

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._q: queue.Queue = queue.Queue()
        self.stopped = False
        self._result.connect(self._deliver)
        threading.Thread(target=self._loop, daemon=True, name="razertux-recorder").start()

    def submit(self, fn: Callable[[], object], done: Callable | None = None,
               failed: Callable | None = None) -> None:
        self._q.put((fn, done, failed))

    def _loop(self) -> None:
        while (item := self._q.get()) is not None:
            fn, done, failed = item
            try:
                value = fn()
            except Exception as e:  # noqa: BLE001 - reported to the page, never raised in the thread
                cb, arg = failed, str(e) or type(e).__name__
            else:
                cb, arg = done, value
            if cb is not None:
                try:
                    self._result.emit(cb, arg)
                except RuntimeError:  # the GUI object is gone (window closed)
                    pass

    def _deliver(self, cb: Callable, arg: object) -> None:
        if not self.stopped:
            cb(arg)

    def shutdown(self) -> None:
        self.stopped = True
        self._q.put(None)


class RecorderSession(QObject):
    signalled = Signal(list)  # one listen signal [source, type, code(, sign)]

    def __init__(self, client: DaemonClient | None, make_backend: Callable[[bool], Backend] = default_backend,
                 clock: Callable[[], float] = time.monotonic, parent: QObject | None = None):
        super().__init__(parent)
        self.client, self.make_backend, self.clock = client, make_backend, clock
        self.worker = SerialWorker(self)
        self._lock = threading.Lock()
        self.closed = False
        self.backend: Backend | None = None
        self.password = False
        self._paused = ""
        self.dev: FoundDevice | None = None
        self.select(None)

    # -- device ------------------------------------------------------------------------
    def select(self, dev: FoundDevice | None) -> None:
        self.disconnect()
        self.dev = dev
        self.info = db.by_pid_any(dev.pid) if dev is not None else None
        self.kind_answer: str | None = None  # Ruling R-4: asked when the database does not know it
        self.template_choice: str | None = None
        self.opened: dict = {}
        self.buttons: dict = {}
        self.reads: dict = {}
        self.light: dict | str = {}
        self.notes = ""

    @property
    def db_kind(self) -> str | None:
        return self.info.kind if self.info is not None else None

    @property
    def kind(self) -> str | None:
        """The database kind, else the user's answer (None: not known)."""
        if self.db_kind is not None:
            return self.db_kind
        return self.kind_answer if self.kind_answer not in (None, "unknown") else None

    @property
    def listen_kind(self) -> str | None:
        """Only sent for devices the database does not know (the helper's own kind wins)."""
        return None if self.db_kind is not None else self.kind

    @property
    def model(self) -> str:
        return self.info.model if self.info is not None else ""

    def file_name(self) -> str:
        return f"razertux-report-{self.model or f'{self.dev.pid:04x}'}.json"

    # -- backend -------------------------------------------------------------------------
    def connect_backend(self, password: bool, done: Callable, failed: Callable) -> None:
        self.disconnect()
        self.password = password
        pid = self.dev.pid

        def job() -> dict:
            be = self.make_backend(password)
            if password:
                be.start()  # pkexec: the password prompt
            try:
                answer = be.open(pid)
            except BaseException:
                be.close()
                raise
            with self._lock:
                if self.closed:
                    be.close()
                    return {}
                self.backend = be
            return answer

        def opened(answer: dict) -> None:
            self.opened = dict(answer)
            done(self.backend.notice if self.backend is not None else "")

        self.worker.submit(job, opened, failed)

    def disconnect(self) -> None:
        with self._lock:
            be, self.backend = self.backend, None
        self.opened = {}
        if be is not None:
            be.request_stop()
            self.worker.submit(be.close)

    def call(self, fn: Callable[[Backend], object], done: Callable | None = None,
             failed: Callable | None = None) -> None:
        """Run fn(backend) on the worker thread (with the backend open now: a close queued
        after it still lets it run, e.g. the light test's "off")."""
        be = self.backend

        def job():
            if be is None:
                raise RuntimeError("no device open")
            return fn(be)
        self.worker.submit(job, done, failed)

    def listen(self, done: Callable, failed: Callable) -> None:
        kind = self.listen_kind
        self.call(lambda be: be.listen(LISTEN_SECONDS, self._emit_signal, kind), done, failed)

    def _emit_signal(self, sig: list) -> None:  # worker thread
        try:
            self.signalled.emit(list(sig))
        except RuntimeError:
            pass

    def stop_listen(self) -> None:
        be = self.backend
        if be is not None:
            be.request_stop()

    # -- daemon pause ----------------------------------------------------------------------
    def pause(self) -> None:
        """Release the daemon's grabs on the device (only for models the daemon handles)."""
        if self._paused or not self.model or self.client is None:
            return
        try:
            if self.client.pause_device(self.model):
                self._paused = self.model
        except DaemonError:
            pass  # not a device the daemon manages

    def refresh_pause(self) -> None:
        """A second pause keeps the daemon's first deadline: resume and pause again."""
        if self._paused:
            self.resume()
            self.pause()

    def resume(self) -> None:
        model, self._paused = self._paused, ""
        if model and self.client is not None:
            try:
                self.client.resume_device(model)
            except DaemonError:
                pass  # the daemon resumes by itself after its timeout

    # -- report ----------------------------------------------------------------------------
    def report(self) -> Report:
        device = report_device(self.dev)
        if self.db_kind is None and self.kind is not None:
            device["kind"] = self.kind  # the user's answer (Ruling R-4)
        return Report(device=device, system=system_section(), interfaces=report_interfaces(self.dev),
                      buttons=dict(self.buttons), reads=dict(self.reads), light=self.light,
                      notes=self.notes)

    def close(self) -> None:
        if self.closed:
            return
        self.resume()
        with self._lock:
            self.closed = True
            be, self.backend = self.backend, None
        if be is not None:
            be.request_stop()
            self.worker.submit(be.close)
        self.worker.shutdown()
