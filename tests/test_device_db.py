from razertux.devices.db import Command, DeviceInfo, by_pid, load_devices


def test_generated_db_naga_v2_pro_matches_hardware_facts():
    d = load_devices()["naga_v2_pro"]
    assert d.verified and d.dpi_max == 30000 and set(d.pids) == {0x00A7, 0x00A8}
    assert d.pids[0x00A7] == "wired" and d.pids[0x00A8] == "wireless"
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
