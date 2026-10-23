import asyncio

from fake_daemon import DA, NAGA, FakeTransport

from razertux.gui.dbus_client import (
    VERSION_MISMATCH, DaemonClient, DaemonError, DaemonUnavailable,
)


def make(t):
    c = DaemonClient(t)
    seen = []
    c.available.connect(seen.append)
    return c, seen


def test_calls_when_up(qapp):
    t = FakeTransport()
    c, seen = make(t)
    assert c.list_profiles() == [("default", "Standard"), ("p2", "Zwei")]
    assert c.active_profile() == "p2"
    assert c.status()["battery"] == 80
    assert c.restore_original() == ["dpi_stages"]
    c.activate_profile("p2")
    assert ("ActivateProfile", (NAGA, "p2")) in t.calls
    assert c.is_available and seen == [True]


def test_unavailable_is_graceful(qapp):
    t = FakeTransport()
    t.up = False
    c, seen = make(t)
    assert c.list_profiles() == []
    assert c.active_profile() == ""
    assert c.status() == {}
    assert c.reload() is False
    assert c.restore_original() is None
    assert not c.is_available
    assert seen == [False]  # emitted once, not per call


def test_recovers_and_signals(qapp):
    t = FakeTransport()
    t.up = False
    c, seen = make(t)
    c.status()
    t.up = True
    c.status()
    assert seen == [False, True]
    got = []
    c.profileChanged.connect(lambda *a: got.append(a))
    c.statusChanged.connect(lambda *a: got.append(a))
    c.handle_signal("ProfileChanged", [DA, "p3"])
    c.handle_signal("StatusChanged", [DA, {"battery": 5}])
    assert got == [(DA, "p3"), (DA, {"battery": 5})]


def test_daemon_error_raises_daemon_error(qapp):
    class T(FakeTransport):
        def call(self, member, signature="", args=()):
            raise DaemonError("io.github.razertux.Error.NoBackup", "Keine Sicherung")

    c, _ = make(T())
    try:
        c.restore_original()
    except DaemonError as e:
        assert "Keine Sicherung" in str(e)
    else:
        raise AssertionError
    assert c.is_available  # an error reply means the daemon is there


def test_refresh_publishes_status_and_active_profile(qapp):
    t = FakeTransport()
    c, _ = make(t)
    got = []
    c.statusChanged.connect(lambda m, s: got.append(("status", m, s["battery"])))
    c.profileChanged.connect(lambda m, p: got.append(("profile", m, p)))
    c.refresh()
    assert got == [("status", NAGA, 80), ("profile", NAGA, "p2")]


def test_real_transport_invalid_bus_address_is_unavailable(qapp, monkeypatch):
    from razertux.gui.dbus_client import DBusNextTransport
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "bogus:nothing")
    t = DBusNextTransport()
    t.start()
    c = DaemonClient(t)
    try:
        assert c.status() == {}
        assert not c.is_available
    finally:
        t.stop()


def test_model_parameter_selects_the_device(qapp):
    t = FakeTransport()
    t.add_second_device()
    c, _ = make(t)
    assert c.devices() == [(NAGA, "Razer Naga V2 Pro", "wireless", True),
                           (DA, "Razer DeathAdder V2", "wired", True)]
    assert c.list_profiles(DA) == [("default", "Standard"), ("dpi", "Präzision")]
    assert c.active_profile(DA) == "dpi" and c.active_profile() == "p2"
    assert c.status(DA)["battery"] == 55
    c.activate_profile("dpi", DA)
    assert ("ActivateProfile", (DA, "dpi")) in t.calls
    assert c.restore_original(DA) == ["dpi_stages"]
    assert ("RestoreOriginal", (DA,)) in t.calls
    assert c.diagnostics(DA) == "probe ok"
    c.start_learning(DA)
    c.stop_learning(DA)
    assert ("StartLearning", (DA,)) in t.calls and ("StopLearning", (DA,)) in t.calls


def test_devices_when_daemon_away(qapp):
    t = FakeTransport()
    t.up = False
    c, _ = make(t)
    assert c.devices() == []
    assert c.diagnostics() == ""


def test_refresh_publishes_every_device(qapp):
    t = FakeTransport()
    t.add_second_device()
    c, _ = make(t)
    got = []
    c.statusChanged.connect(lambda m, s: got.append(m))
    c.refresh()
    assert got == [NAGA, DA]


def test_new_signals(qapp):
    c, _ = make(FakeTransport())
    got = []
    c.devicesChanged.connect(lambda: got.append("devices"))
    c.buttonLearned.connect(lambda *a: got.append(a))
    c.handle_signal("DevicesChanged", [])
    c.handle_signal("ButtonLearned", [DA, "mouse", 1, 272])
    assert got == ["devices", (DA, "mouse", 1, 272)]


def test_old_daemon_without_daemon1_shows_version_hint(qapp):
    class Old(FakeTransport):
        def call(self, member, signature="", args=()):
            raise DaemonUnavailable(VERSION_MISMATCH)

    c, seen = make(Old())
    assert c.status() == {} and c.devices() == [] and c.list_profiles() == []
    assert not c.is_available and c.version_mismatch
    assert c.unavailable_text() == "Dienst-Version passt nicht – bitte RazerTux neu starten"
    assert seen == [False]


def test_version_mismatch_clears_when_daemon_answers(qapp):
    t = FakeTransport()
    t.up = False
    c, _ = make(t)
    c.status()
    assert not c.version_mismatch and c.unavailable_text() == "Dienst läuft nicht"
    t.up = True
    assert c.version() == "1"
    assert c.is_available


class _Bus:
    """dbus-next bus whose service has only the old interface."""

    connected = True

    def __init__(self, error):
        self.error = error

    async def call(self, msg):
        from dbus_next import Message, MessageType
        return Message(message_type=MessageType.ERROR, error_name=self.error, reply_serial=1,
                       signature="s", body=["no such thing"])


def _real_call(error):
    from razertux.gui.dbus_client import DBusNextTransport
    t = DBusNextTransport()

    async def connect():
        return _Bus(error)
    t._connect = connect
    try:
        return asyncio.run(t._call("GetVersion", "", ()))
    finally:
        t._loop.close()


def test_real_transport_maps_missing_daemon1_to_version_hint():
    for error in ("org.freedesktop.DBus.Error.UnknownMethod",
                  "org.freedesktop.DBus.Error.UnknownInterface"):
        try:
            _real_call(error)
        except DaemonUnavailable as e:
            assert str(e) == VERSION_MISMATCH
        else:
            raise AssertionError(error)


def test_real_transport_other_errors_stay_daemon_errors():
    try:
        _real_call("io.github.razertux.Error.UnknownDevice")
    except DaemonError as e:
        assert e.name.endswith("UnknownDevice")
    else:
        raise AssertionError
