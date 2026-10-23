from razertux.devices.db import Command, DeviceInfo, by_pid, load_devices


def test_generated_db_naga_v2_pro_matches_hardware_facts():
    model = load_devices()["naga_v2_pro"]
    assert model.verified and model.dpi_max == 30000 and set(model.pids) == {0x00A7, 0x00A8}
    assert model.pids[0x00A7] == "wired" and model.pids[0x00A8] == "wireless"
    for pid in (0x00A7, 0x00A8):
        d = model.for_pid(pid)
        assert d.protocol["set_dpi"].txn == 0x1F and "battery" in d.features


def test_only_naga_v2_pro_verified():
    assert [m for m, d in load_devices().items() if d.verified] == ["naga_v2_pro"]


def test_db_has_all_openrazer_mice():
    devs = load_devices()
    assert len(devs) >= 60 and sum(len(d.pids) for d in devs.values()) >= 117


def test_by_pid():
    assert by_pid(0x00A8).model == "naga_v2_pro"
    assert by_pid(0xFFFF) is None


def test_types_are_hashable_and_immutable():
    d = load_devices()["naga_v2_pro"]
    assert isinstance(d, DeviceInfo) and isinstance(d.features, frozenset)
    assert isinstance(d.protocol["set_dpi"], Command)
    assert isinstance(d.poll_rates, tuple)
    assert all(isinstance(v, tuple) for v in d.zones.values())


def test_zones_of_naga_v2_pro():
    d = load_devices()["naga_v2_pro"]
    assert "logo" in d.zones and "static" in d.zones["logo"]
    assert "get_brightness:logo" in d.protocol


def test_for_pid_flattens_common_and_pid_entries():
    c1, c2 = Command("f", 0x3F), Command("f", 0x1F)
    info = DeviceInfo(
        model="m", name="M", pids={1: "wired", 2: "wireless"}, dpi_max=1, poll_rates=(),
        features=frozenset({"dpi"}), zones={}, protocol={"get_poll_rate": Command("p", 0x1F)},
        pid_protocol={1: {"get_dpi": c1}, 2: {"get_dpi": c2}},
        pid_features={2: frozenset({"battery"})},
    )
    assert "get_dpi" not in info.protocol and "battery" not in info.features
    w, wl = info.for_pid(1), info.for_pid(2)
    assert w.protocol["get_dpi"] == c1 and wl.protocol["get_dpi"] == c2
    assert "battery" not in w.features and "battery" in wl.features
    assert wl.pid_protocol == {} and wl.pid_features == {} and "get_poll_rate" in wl.protocol
    try:
        info.for_pid(3)
    except KeyError:
        pass
    else:
        raise AssertionError("unknown pid must raise")


def test_command_steps_default_to_fn():
    assert Command("a", 1).steps == ("a",)
    assert Command("a", 1, ("raw:0x0f/0x02/0x06", "a")).fn == "a"


def test_naga_v2_pro_thumbgrid_effects_are_multi_step_or_raw():
    d = load_devices()["naga_v2_pro"].for_pid(0x00A7)
    c = d.protocol["set_effect:matrix:static"]
    assert c.steps[0].startswith("raw:0x0f/0x02/0x06") and len(c.steps) == 2
