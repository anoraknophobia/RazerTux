"""Task 12: "Taste anlernen" (LearnDialog) and its entry in the action dialog."""
import pytest
from fake_daemon import DA, FakeTransport
from PySide6.QtWidgets import QDialog

from razertux.config import ConfigStore
from razertux.config.model import DefaultAction
from razertux.gui.action_dialog import ActionDialog
from razertux.gui.dbus_client import DaemonClient, DaemonError
from razertux.gui.learn_dialog import LEARN_TIMEOUT_MS, LearnDialog
from razertux.hw.button_maps import InputSignal
from razertux.hw.buttons import ButtonId


@pytest.fixture
def env(qapp, tmp_path):
    transport = FakeTransport()
    transport.add_second_device()
    client = DaemonClient(transport)
    ms = ConfigStore(tmp_path).for_model(DA)
    d = LearnDialog(client, DA, ButtonId.LEARN_1, ms)
    yield d, transport, ms
    d.close()


def test_start_asks_the_daemon_and_shows_the_prompt(env):
    d, transport, _ = env
    d.start()
    assert ("StartLearning", (DA,)) in transport.calls
    assert d.message.text() == "Drück jetzt die Taste an der Maus …"
    assert d.timer.isActive() and d.timer.interval() == LEARN_TIMEOUT_MS == 15000


def test_learned_signal_is_saved_and_reloaded(env):
    d, transport, ms = env
    d.start()
    d.client.handle_signal("ButtonLearned", ["naga_v2_pro", "if02", 1, 2])  # other mouse
    assert ms.load_device_settings().learned == {}
    d.client.handle_signal("ButtonLearned", [DA, "mouse", 1, 275])
    assert ms.load_device_settings().learned == {ButtonId.LEARN_1: InputSignal("mouse", 1, 275)}
    assert ("Reload", ()) in transport.calls
    assert d.result() == QDialog.DialogCode.Accepted and not d.timer.isActive()


def test_relearned_signal_leaves_its_old_button(env):
    d, _, ms = env
    ds = ms.load_device_settings()
    ds.learned = {ButtonId.LEARN_2: InputSignal("mouse", 1, 275)}
    ms.save_device_settings(ds)
    d.start()
    d.client.handle_signal("ButtonLearned", [DA, "mouse", 1, 275])
    assert ms.load_device_settings().learned == {ButtonId.LEARN_1: InputSignal("mouse", 1, 275)}


def test_cancel_stops_learning(env):
    d, transport, ms = env
    d.start()
    d.cancel_btn.click()
    assert ("StopLearning", (DA,)) in transport.calls
    assert d.result() == QDialog.DialogCode.Rejected
    d.client.handle_signal("ButtonLearned", [DA, "mouse", 1, 275])  # too late
    assert ms.load_device_settings().learned == {}


def test_timeout_stops_learning_and_says_so(env):
    d, transport, ms = env
    d.start()
    d.timer.timeout.emit()
    assert ("StopLearning", (DA,)) in transport.calls
    assert "Keine Taste erkannt" in d.message.text()
    d.client.handle_signal("ButtonLearned", [DA, "mouse", 1, 275])
    assert ms.load_device_settings().learned == {}


def test_disconnected_mouse_gets_a_message_not_a_learn_call(env):
    d, transport, _ = env
    transport.statuses[DA] = {"connected": False}
    d.start()
    assert not any(c[0] == "StartLearning" for c in transport.calls)
    assert d.message.text() == "Die Maus ist nicht verbunden – zum Anlernen bitte anschließen."
    assert not d.timer.isActive()


def test_daemon_error_becomes_a_message(env):
    d, transport, _ = env
    orig = transport.call

    def failing(member, signature="", args=()):
        if member == "StartLearning":
            raise DaemonError("io.github.razertux.Error.NotConnected", "Maus nicht verbunden")
        return orig(member, signature, args)
    transport.call = failing
    d.start()  # no traceback
    assert "Maus nicht verbunden" in d.message.text() and not d.timer.isActive()


def test_daemon_away_becomes_a_message(env):
    d, transport, _ = env
    transport.up = False
    d.start()
    assert d.message.text() == "Der Dienst läuft nicht – Anlernen ist nur mit laufendem Dienst möglich."


def test_action_dialog_offers_learning_only_when_asked(qapp):
    plain = ActionDialog(None, DefaultAction(), {}, button=ButtonId.SIDE_1)
    assert plain.learn_btn is None
    calls = []
    d = ActionDialog(None, DefaultAction(), {}, button=ButtonId.SIDE_1, on_learn=lambda: calls.append(1))
    assert d.learn_btn.text() == "Taste anlernen …"
    d.learn_btn.click()
    assert calls == [1]
