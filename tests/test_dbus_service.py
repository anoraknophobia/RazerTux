"""Daemon1 D-Bus service tested directly (no real bus): methods via dbus-next's
method table, signals via a fake bus registered on the interface. Real sessions
(no mouse connected), so activation really lands in the right DeviceSession."""
import inspect
import json

import pytest
from dbus_next import DBusError
from dbus_next.service import ServiceInterface
from dbus_next.signature import SignatureTree
from test_daemon_devices import RecOut

from razertux.config.model import KeyAction, Profile, Settings
from razertux.config.store import ConfigStore
from razertux.daemon.dbus_service import BUS_NAME, INTERFACE, OBJECT_PATH, VERSION
from razertux.daemon.main import Daemon
from razertux.devices.db import load_devices
from razertux.hw.button_maps import InputSignal
from razertux.hw.buttons import ButtonId

NAGA, DA = "naga_v2_pro", "death_adder_v2"


class FakeBus:
    def __init__(self):
        self.signals = []  # (member, body)

    def _interface_signal_notify(self, iface, iface_name, member, signature, body, fds):
        SignatureTree(signature).verify(body)  # raises on a type mismatch
        assert iface_name == INTERFACE
        self.signals.append((member, body))


async def call(svc, member, *args):
    """Invoke a D-Bus method like the bus would; verify the reply signature."""
    m = next(m for m in ServiceInterface._get_methods(svc) if m.name == member)
    result = m.fn(svc, *args)
    if inspect.isawaitable(result):
        result = await result
    body, _fds = ServiceInterface._fn_result_to_body(result, m.out_signature_tree)
    m.out_signature_tree.verify(body)
    return result


def make(tmp_path):
    store = ConfigStore(tmp_path)
    store.for_model(NAGA).save_profile(Profile(id="game", name="Spiel",
                                               standard={ButtonId.SIDE_1: KeyAction(30)}))
    store.for_model(DA).save_profile(Profile(id="p", name="Pee"))
    store.save_settings(Settings(active_profile="default"))
    d = Daemon(store, RecOut(), openrazer_loaded=lambda: False, retry_delay=0)
    d._session(load_devices()[DA])
    svc = d.service
    bus = FakeBus()
    ServiceInterface._add_bus(svc, bus)
    return d, svc, store, bus


def test_names():
    assert BUS_NAME == "io.github.razertux"
    assert OBJECT_PATH == "/io/github/razertux"
    assert INTERFACE == "io.github.razertux.Daemon1"


async def test_get_version(tmp_path):
    d, svc, *_ = make(tmp_path)
    assert await call(svc, "GetVersion") == VERSION


async def test_list_devices_includes_disconnected_sessions(tmp_path):
    d, svc, *_ = make(tmp_path)
    devices = await call(svc, "ListDevices")
    assert devices == [[NAGA, load_devices()[NAGA].name, "", False],
                       [DA, load_devices()[DA].name, "", False]]
    d.sessions[DA]._hw = {**d.sessions[DA]._hw, "connected": True, "connection": "wired"}
    assert [DA, load_devices()[DA].name, "wired", True] in await call(svc, "ListDevices")


async def test_list_profiles_per_model(tmp_path):
    d, svc, *_ = make(tmp_path)
    assert await call(svc, "ListProfiles", NAGA) == [["game", "Spiel"]]
    assert await call(svc, "ListProfiles", DA) == [["p", "Pee"]]


async def test_unknown_model_raises_dbus_error(tmp_path):
    d, svc, *_ = make(tmp_path)
    for member, args in (("ListProfiles", ("nope",)), ("GetStatus", ("nope",)),
                         ("ActivateProfile", ("nope", "p")), ("GetDiagnostics", ("nope",))):
        with pytest.raises(DBusError) as ei:
            await call(svc, member, *args)
        assert ei.value.type == "io.github.razertux.Error.UnknownDevice"


async def test_activate_profile_lands_in_the_right_session(tmp_path):
    d, svc, store, bus = make(tmp_path)
    await call(svc, "ActivateProfile", DA, "p")
    assert d.sessions[DA].profile.id == "p"
    assert d.sessions[NAGA].profile is None
    active = json.loads((tmp_path / "settings.json").read_text())["active_profiles"]
    assert active[DA] == "p" and active.get(NAGA, "default") == "default"
    assert ("ProfileChanged", [DA, "p"]) in bus.signals
    assert await call(svc, "GetActiveProfile", DA) == "p"
    assert await call(svc, "GetActiveProfile", NAGA) == "default"


async def test_activate_unknown_profile_raises_dbus_error(tmp_path):
    d, svc, store, bus = make(tmp_path)
    with pytest.raises(DBusError) as ei:
        await call(svc, "ActivateProfile", NAGA, "nope")
    assert ei.value.type == "io.github.razertux.Error.UnknownProfile"
    assert d.sessions[NAGA].profile is None
    assert bus.signals == []
    assert json.loads((tmp_path / "settings.json").read_text())["active_profiles"][NAGA] == "default"


async def test_profile_of_other_model_is_unknown(tmp_path):
    d, svc, *_ = make(tmp_path)
    with pytest.raises(DBusError) as ei:
        await call(svc, "ActivateProfile", DA, "game")
    assert ei.value.type == "io.github.razertux.Error.UnknownProfile"


async def test_default_profile_always_activatable(tmp_path):
    d, svc, *_ = make(tmp_path)
    await call(svc, "ActivateProfile", NAGA, "game")
    await call(svc, "ActivateProfile", NAGA, "default")
    assert d.sessions[NAGA].profile.id == "default"


async def test_get_status_disconnected(tmp_path):
    d, svc, *_ = make(tmp_path)
    st = await call(svc, "GetStatus", NAGA)
    plain = {k: v.value for k, v in st.items()}
    assert plain == {
        "connected": False, "connection": "", "battery": -1, "charging": False,
        "scroll_stage": -1, "dpi": -1, "openrazer_loaded": False, "config_errors": [],
        "writable": False, "paused": False, "experimental": False, "model": NAGA,
        "name": load_devices()[NAGA].name,
    }
    assert st["battery"].signature == "i" and st["config_errors"].signature == "as"
    assert st["writable"].signature == "b" and st["name"].signature == "s"


async def test_get_status_connected_experimental(tmp_path):
    d, svc, *_ = make(tmp_path)
    s = d.sessions[DA]
    s._hw = {**s._hw, "connected": True, "connection": "wired", "battery": 80, "dpi": 1600}
    st = {k: v.value for k, v in (await call(svc, "GetStatus", DA)).items()}
    assert (st["connected"], st["battery"], st["dpi"]) == (True, 80, 1600)
    assert st["experimental"] is (not load_devices()[DA].verified)
    assert st["model"] == DA


def test_scroll_profile_method_removed(tmp_path):
    d, svc, *_ = make(tmp_path)
    assert "ScrollProfileSupported" not in {m.name for m in ServiceInterface._get_methods(svc)}


async def test_reload_switches_profile_and_reports_errors(tmp_path):
    d, svc, store, bus = make(tmp_path)
    (tmp_path / "settings.json").write_text('{"active_profile": "game"}')
    (tmp_path / "macros.json").write_text("{kaputt")
    await call(svc, "Reload")
    assert d.sessions[NAGA].profile.id == "game"
    assert ("ProfileChanged", [NAGA, "game"]) in bus.signals
    errors = (await call(svc, "GetStatus", NAGA))["config_errors"].value
    assert any("macros.json" in e for e in errors)
    assert any(m == "StatusChanged" and b[0] == NAGA for m, b in bus.signals)


async def test_reload_same_profile_no_profile_changed(tmp_path):
    d, svc, store, bus = make(tmp_path)
    await call(svc, "Reload")
    bus.signals.clear()
    await call(svc, "Reload")
    assert d.sessions[NAGA].profile.id == "default"
    assert not any(m == "ProfileChanged" for m, _ in bus.signals)


async def test_reload_clears_old_errors(tmp_path):
    d, svc, *_ = make(tmp_path)
    (tmp_path / "macros.json").write_text("{kaputt")
    await call(svc, "Reload")
    (tmp_path / "macros.json").write_text("{}")
    await call(svc, "Reload")
    assert (await call(svc, "GetStatus", NAGA))["config_errors"].value == []


async def test_restore_original_without_backup(tmp_path):
    d, svc, *_ = make(tmp_path)
    with pytest.raises(DBusError) as ei:
        await call(svc, "RestoreOriginal", NAGA)
    assert ei.value.type == "io.github.razertux.Error.NoBackup"


async def test_restore_original_not_connected(tmp_path):
    d, svc, *_ = make(tmp_path)
    d.sessions[DA].backup_path.parent.mkdir(parents=True, exist_ok=True)
    d.sessions[DA].backup_path.write_text('{"dpi": [800, 800]}')
    with pytest.raises(DBusError) as ei:
        await call(svc, "RestoreOriginal", DA)
    assert ei.value.type == "io.github.razertux.Error.NotConnected"


async def test_restore_original_goes_to_the_models_session(tmp_path):
    d, svc, *_ = make(tmp_path)
    seen = []
    d.sessions[DA].restore_backup = lambda b: (seen.append(b), ["poll_rate", "dpi"])[1]
    d.sessions[DA].backup_path.parent.mkdir(parents=True, exist_ok=True)
    d.sessions[DA].backup_path.write_text('{"dpi": [800, 800]}')
    assert await call(svc, "RestoreOriginal", DA) == ["poll_rate", "dpi"]
    assert seen == [{"dpi": [800, 800]}]


async def test_get_diagnostics(tmp_path):
    d, svc, *_ = make(tmp_path)
    assert await call(svc, "GetDiagnostics", DA) == ""
    d.sessions[DA].diagnostics = lambda: "probe: ok"
    assert await call(svc, "GetDiagnostics", DA) == "probe: ok"


async def test_learning_maps_to_session_and_emits_button_learned(tmp_path):
    d, svc, store, bus = make(tmp_path)
    await call(svc, "StartLearning", DA)
    assert d.sessions[DA].status()["learning"] is True
    assert d.sessions[NAGA].status()["learning"] is False
    d.sessions[DA].on_learned(InputSignal("mouse", 1, 272))
    assert ("ButtonLearned", [DA, "mouse", 1, 272]) in bus.signals
    await call(svc, "StopLearning", DA)
    assert d.sessions[DA].status()["learning"] is False


async def test_notify_status_emits_signal_with_model(tmp_path):
    d, svc, store, bus = make(tmp_path)
    svc.notify_status(DA)
    member, body = bus.signals[-1]
    assert member == "StatusChanged" and body[0] == DA
    assert body[1]["model"].value == DA


async def test_devices_changed_when_connection_changes(tmp_path):
    d, svc, store, bus = make(tmp_path)
    svc.notify_status(DA)
    assert not any(m == "DevicesChanged" for m, _ in bus.signals[1:])
    s = d.sessions[DA]
    s._hw = {**s._hw, "connected": True, "connection": "wired"}
    svc.notify_status(DA)
    assert ("DevicesChanged", []) in bus.signals


async def test_activate_keeps_settings_saved_by_gui(tmp_path):
    d, svc, store, bus = make(tmp_path)
    await svc.reload()
    store.save_settings(Settings(active_profile="default", theme="system",
                                 low_battery_notify=30))  # GUI saved meanwhile
    await call(svc, "ActivateProfile", NAGA, "game")
    s = store.load_settings()
    assert (s.active_profile, s.theme, s.low_battery_notify) == ("game", "system", 30)


# -- PauseDevice / ResumeDevice -----------------------------------------------------------

def grabbed(d, model=NAGA):
    return d.sessions[model].router.grabbed


async def test_pause_resume_methods_toggle_grab_and_are_idempotent(tmp_path):
    d, svc, *_ = make(tmp_path)
    assert grabbed(d)
    await call(svc, "PauseDevice", NAGA)
    await call(svc, "PauseDevice", NAGA)
    assert not grabbed(d) and (await call(svc, "GetStatus", NAGA))["paused"].value is True
    await call(svc, "ResumeDevice", NAGA)
    await call(svc, "ResumeDevice", NAGA)
    assert grabbed(d)


async def test_pause_unknown_model_raises(tmp_path):
    d, svc, *_ = make(tmp_path)
    for member in ("PauseDevice", "ResumeDevice"):
        with pytest.raises(DBusError) as e:
            await call(svc, member, "nope")
        assert e.value.type.endswith("UnknownDevice")


async def test_pause_only_affects_that_model(tmp_path):
    d, svc, *_ = make(tmp_path)
    before = grabbed(d, DA)
    await call(svc, "PauseDevice", NAGA)
    assert grabbed(d, DA) == before
    await call(svc, "ResumeDevice", NAGA)


async def test_caller_vanishing_resumes(tmp_path):
    d, svc, *_ = make(tmp_path)
    await svc.pause_device(NAGA, ":1.42")
    await svc.name_owner_changed(":1.99", ":1.99", "")  # somebody else: still paused
    assert not grabbed(d)
    await svc.name_owner_changed(":1.42", ":1.42", "")
    assert grabbed(d)


async def test_caller_vanishing_after_explicit_resume_is_harmless(tmp_path):
    d, svc, *_ = make(tmp_path)
    await svc.pause_device(NAGA, ":1.42")
    await call(svc, "ResumeDevice", NAGA)
    await svc.name_owner_changed(":1.42", ":1.42", "")
    assert grabbed(d)


async def test_pause_times_out_and_regrabs(tmp_path):
    import asyncio
    d, svc, *_ = make(tmp_path)
    svc.pause_timeout = 0.05
    await call(svc, "PauseDevice", NAGA)
    assert not grabbed(d)
    await asyncio.sleep(0.2)
    assert grabbed(d)


async def test_resume_cancels_timeout(tmp_path):
    import asyncio
    d, svc, *_ = make(tmp_path)
    svc.pause_timeout = 0.05
    await call(svc, "PauseDevice", NAGA)
    await call(svc, "ResumeDevice", NAGA)
    await call(svc, "PauseDevice", DA)  # unrelated pause must not be touched by NAGA's timer
    await asyncio.sleep(0.2)
    assert grabbed(d)


async def test_shutdown_while_paused_is_clean(tmp_path):
    d, svc, *_ = make(tmp_path)
    svc.pause_timeout = 0.05
    await call(svc, "PauseDevice", NAGA)
    await d.shutdown()
    assert not svc._pauses  # timers cancelled
    import asyncio
    await asyncio.sleep(0.1)  # no timer fires on closed sessions


async def test_profile_switch_while_paused_stays_released(tmp_path):
    d, svc, *_ = make(tmp_path)
    await call(svc, "PauseDevice", NAGA)
    await call(svc, "ActivateProfile", NAGA, "game")
    await call(svc, "Reload")
    assert not grabbed(d)
    await call(svc, "ResumeDevice", NAGA)
    assert grabbed(d)


async def test_unknown_or_missing_sender_never_resumes_on_other_exits(tmp_path):
    d, svc, *_ = make(tmp_path)
    await call(svc, "PauseDevice", NAGA)  # no sender known
    await svc.name_owner_changed(":1.7", ":1.7", "")
    assert not grabbed(d)
    await call(svc, "ResumeDevice", NAGA)


async def test_failed_resume_keeps_pause_state(tmp_path):
    d, svc, *_ = make(tmp_path)
    await svc.pause_device(NAGA, ":1.1")
    s = d.sessions[NAGA]
    real = s.resume

    async def boom():
        raise OSError("x")
    s.resume = boom
    with pytest.raises(OSError):
        await svc.resume_device(NAGA)
    assert NAGA in svc._pauses
    s.resume = real
    await svc.resume_device(NAGA)
    assert grabbed(d) and NAGA not in svc._pauses
