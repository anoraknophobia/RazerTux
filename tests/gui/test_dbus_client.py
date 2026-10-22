from fake_daemon import FakeTransport
from naga_config.gui.dbus_client import DaemonClient, DaemonError, DaemonUnavailable


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
    assert c.restore_original() == ["dpi"]
    c.activate_profile("p2")
    assert ("ActivateProfile", ("p2",)) in t.calls
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
    c.profileChanged.connect(got.append)
    c.statusChanged.connect(got.append)
    c.handle_signal("ProfileChanged", ["p3"])
    c.handle_signal("StatusChanged", [{"battery": 5}])
    assert got == ["p3", {"battery": 5}]


def test_daemon_error_raises_daemon_error(qapp):
    class T(FakeTransport):
        def call(self, member, signature="", args=()):
            raise DaemonError("io.github.nagaconfig.Error.NoBackup", "Keine Sicherung")

    c, _ = make(T())
    try:
        c.restore_original()
    except DaemonError as e:
        assert "Keine Sicherung" in str(e)
    else:
        raise AssertionError
    assert c.is_available  # an error reply means the daemon is there
