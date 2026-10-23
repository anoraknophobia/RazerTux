import os
import time

from PySide6.QtCore import QCoreApplication

from razertux.gui.single_instance import InstanceServer, notify_running, wait_until_gone


def _name():
    return f"razertux-test-{os.getpid()}-{time.monotonic_ns()}"


def _pump(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not cond():
        QCoreApplication.processEvents()
        time.sleep(0.01)
    return cond()


def test_no_instance_running(qapp):
    name = _name()
    assert notify_running(show=True, name=name) is False
    assert wait_until_gone(name, timeout=0.5) is True


def test_second_start_shows_window_of_first(qapp):
    name = _name()
    server = InstanceServer(name)
    assert server.listening
    shown = []
    server.showRequested.connect(lambda: shown.append(1))
    assert notify_running(show=True, name=name) is True
    assert _pump(lambda: shown == [1])
    server.close()


def test_tray_start_only_pings(qapp):
    name = _name()
    server = InstanceServer(name)
    shown = []
    server.showRequested.connect(lambda: shown.append(1))
    assert notify_running(show=False, name=name) is True
    _pump(lambda: False, timeout=0.2)
    assert shown == []
    server.close()


def test_wait_until_gone_times_out_while_running(qapp):
    name = _name()
    server = InstanceServer(name)
    assert wait_until_gone(name, timeout=0.3) is False
    server.close()
    assert wait_until_gone(name, timeout=1.0) is True
