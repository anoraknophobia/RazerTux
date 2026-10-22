"""D-Bus service object tested directly (no real bus): methods via dbus-next's
method table, signals via a fake bus registered on the interface."""
import inspect
import json

import pytest
from dbus_next import DBusError
from dbus_next.service import ServiceInterface
from dbus_next.signature import SignatureTree

from naga_config.config.model import KeyAction, Profile, Settings
from naga_config.config.store import ConfigStore
from naga_config.daemon.dbus_service import (
    BUS_NAME,
    INTERFACE,
    OBJECT_PATH,
    NagaService,
)
from naga_config.hw.buttons import ButtonId


class FakeBus:
    def __init__(self):
        self.signals = []  # (member, body)

    def _interface_signal_notify(self, iface, iface_name, member, signature, body, fds):
        SignatureTree(signature).verify(body)  # raises on a type mismatch
        assert iface_name == INTERFACE
        self.signals.append((member, body))


class FakeMapper:
    def __init__(self):
        self.profiles = []
        self.macros = []

    async def set_profile(self, profile):
        self.profiles.append(profile)

    def set_macros(self, macros):
        self.macros.append(macros)


class FakeHw:
    def __init__(self, connected=True, connection="wired", battery=80, charging=False,
                 scroll=True):
        self.state = {"connected": connected, "connection": connection,
                      "battery": battery, "charging": charging}
        self.scroll = scroll

    def hw_status(self):
        return dict(self.state)

    def scroll_profile_supported(self):
        return self.scroll


async def call(svc, member, *args):
    """Invoke a D-Bus method like the bus would; verify the reply signature."""
    m = next(m for m in ServiceInterface._get_methods(svc) if m.name == member)
    result = m.fn(svc, *args)
    if inspect.isawaitable(result):
        result = await result
    body, _fds = ServiceInterface._fn_result_to_body(result, m.out_signature_tree)
    m.out_signature_tree.verify(body)
    return result


def make(tmp_path, **kw):
    store = ConfigStore(tmp_path)
    store.save_profile(Profile(id="game", name="Spiel",
                               standard={ButtonId.SIDE_1: KeyAction(30)}))
    store.save_settings(Settings(active_profile="default"))
    mapper = FakeMapper()
    hw = kw.pop("hw", FakeHw())
    applied = []
    svc = NagaService(store, mapper, hw, apply_device_settings=applied.append,
                      openrazer_loaded=lambda: False,
                      backup_path=tmp_path / "mouse-backup.json", **kw)
    bus = FakeBus()
    ServiceInterface._add_bus(svc, bus)
    return svc, store, mapper, bus, applied


def test_names():
    assert BUS_NAME == "io.github.nagaconfig"
    assert OBJECT_PATH == "/io/github/nagaconfig"
    assert INTERFACE == "io.github.nagaconfig.Daemon"


async def test_activate_profile_sets_mapper_saves_and_signals(tmp_path):
    svc, store, mapper, bus, applied = make(tmp_path)
    await call(svc, "ActivateProfile", "game")
    assert [p.id for p in mapper.profiles] == ["game"]
    assert [p.id for p in applied] == ["game"]
    assert json.loads((tmp_path / "settings.json").read_text())["active_profile"] == "game"
    assert ("ProfileChanged", ["game"]) in bus.signals
    assert await call(svc, "GetActiveProfile") == "game"


async def test_activate_unknown_profile_raises_dbus_error(tmp_path):
    svc, store, mapper, bus, _ = make(tmp_path)
    with pytest.raises(DBusError) as ei:
        await call(svc, "ActivateProfile", "nope")
    assert ei.value.type == "io.github.nagaconfig.Error.UnknownProfile"
    assert mapper.profiles == []
    assert bus.signals == []
    assert json.loads((tmp_path / "settings.json").read_text())["active_profile"] == "default"


async def test_list_profiles(tmp_path):
    svc, *_ = make(tmp_path)
    assert await call(svc, "ListProfiles") == [["game", "Spiel"]]


async def test_default_profile_always_listed_and_activatable(tmp_path):
    svc, store, mapper, bus, _ = make(tmp_path)
    await call(svc, "ActivateProfile", "game")
    await call(svc, "ActivateProfile", "default")
    assert mapper.profiles[-1].id == "default"


async def test_get_status_connected(tmp_path):
    svc, *_ = make(tmp_path)
    st = await call(svc, "GetStatus")
    assert {k: v.value for k, v in st.items()} == {
        "connected": True, "connection": "wired", "battery": 80, "charging": False,
        "openrazer_loaded": False, "config_errors": [],
    }
    assert st["battery"].signature == "i" and st["config_errors"].signature == "as"


async def test_get_status_disconnected(tmp_path):
    hw = FakeHw(connected=False, connection="", battery=-1, charging=False, scroll=False)
    svc, *_ = make(tmp_path, hw=hw)
    st = await call(svc, "GetStatus")
    assert st["connected"].value is False
    assert st["connection"].value == ""
    assert st["battery"].value == -1
    assert await call(svc, "ScrollProfileSupported") is False


async def test_reload_switches_profile_and_reports_errors(tmp_path):
    svc, store, mapper, bus, _ = make(tmp_path)
    (tmp_path / "settings.json").write_text('{"active_profile": "game"}')
    (tmp_path / "macros.json").write_text("{kaputt")
    await call(svc, "Reload")
    assert mapper.profiles[-1].id == "game"
    assert mapper.macros[-1] == {}
    assert ("ProfileChanged", ["game"]) in bus.signals
    errors = (await call(svc, "GetStatus"))["config_errors"].value
    assert any("macros.json" in e for e in errors)
    assert any(m == "StatusChanged" for m, _ in bus.signals)


async def test_reload_same_profile_no_profile_changed(tmp_path):
    svc, store, mapper, bus, _ = make(tmp_path)
    await call(svc, "Reload")
    assert mapper.profiles[-1].id == "default"
    assert not any(m == "ProfileChanged" for m, _ in bus.signals)


async def test_reload_clears_old_errors(tmp_path):
    svc, *_ = make(tmp_path)
    (tmp_path / "macros.json").write_text("{kaputt")
    await call(svc, "Reload")
    (tmp_path / "macros.json").write_text("{}")
    await call(svc, "Reload")
    assert (await call(svc, "GetStatus"))["config_errors"].value == []


async def test_restore_original_without_backup(tmp_path):
    svc, *_ = make(tmp_path)
    with pytest.raises(DBusError) as ei:
        await call(svc, "RestoreOriginal")
    assert ei.value.type == "io.github.nagaconfig.Error.NoBackup"


async def test_restore_original_not_implemented_yet(tmp_path):
    svc, *_ = make(tmp_path)
    (tmp_path / "mouse-backup.json").write_text('{"dpi": [800, 800]}')
    with pytest.raises(DBusError) as ei:
        await call(svc, "RestoreOriginal")
    assert ei.value.type == "io.github.nagaconfig.Error.NotImplemented"


async def test_restore_original_calls_hook(tmp_path):
    seen = []
    svc, *_ = make(tmp_path, restore=seen.append)
    (tmp_path / "mouse-backup.json").write_text('{"dpi": [800, 800]}')
    assert await call(svc, "RestoreOriginal") == []
    assert seen == [{"dpi": [800, 800]}]


async def test_restore_original_returns_unrestored_fields(tmp_path):
    svc, *_ = make(tmp_path, restore=lambda b: ["poll_rate", "dpi"])
    (tmp_path / "mouse-backup.json").write_text('{"dpi": [800, 800]}')
    assert await call(svc, "RestoreOriginal") == ["poll_rate", "dpi"]


async def test_scroll_profile_supported(tmp_path):
    svc, *_ = make(tmp_path)
    assert await call(svc, "ScrollProfileSupported") is True


async def test_notify_status_emits_signal(tmp_path):
    svc, store, mapper, bus, _ = make(tmp_path)
    svc.notify_status()
    member, body = bus.signals[-1]
    assert member == "StatusChanged"
    assert body[0]["connected"].value is True
