"""In-memory stand-in for the OpenRazer daemon on D-Bus (razertux.openrazer.client.Bus)."""
import asyncio

from razertux.openrazer.client import OpenRazerError

# What OpenRazer 3.12.4 offers the Naga V2 Pro (docs/hardware-notes.md)
NAGA_METHODS = {
    "razer.device.dpi": {"getDPI", "setDPI", "getDPIStages", "setDPIStages", "maxDPI"},
    "razer.device.misc": {"getVidPid", "getSerial", "getFirmware", "getPollRate", "setPollRate",
                          "getDeviceMode", "setDeviceMode"},
    "razer.device.power": {"getBattery", "isCharging", "getIdleTime", "setIdleTime",
                           "getLowBatteryThreshold", "setLowBatteryThreshold"},
    "razer.device.lighting.logo": {"setLogoNone", "setLogoStatic", "setLogoBreathSingle",
                                   "setLogoBreathDual", "setLogoBreathRandom", "setLogoReactive",
                                   "setLogoSpectrum", "setLogoWave", "setLogoBrightness",
                                   "getLogoBrightness", "getLogoEffect"},
    "razer.device.lighting.chroma": {"setNone", "setStatic", "setSpectrum"},
    "razer.device.lighting.brightness": {"getBrightness", "setBrightness"},
}


class FakeOrDev:
    def __init__(self, pid, methods=None, values=None, fail=()):
        self.pid = pid
        self.methods = {k: set(v) for k, v in (methods or NAGA_METHODS).items()}
        self.values = {"getBattery": 50.0, "isCharging": False, "getDPI": [1600, 1600],
                       "getFirmware": "v1.0", "getSerial": "PM0000X00000000",
                       "getPollRate": 1000, **(values or {})}
        self.fail = set(fail)  # member names that answer with an error


class FakeOpenRazerBus:
    def __init__(self, devices=None, owner=True):
        self.devices = dict(devices or {})  # serial -> FakeOrDev
        self.owner = owner
        self.hang = False
        self.calls = []  # (path, interface, member, args)
        self._owner_cbs = []

    def set_owner(self, up):
        self.owner = up
        for cb in list(self._owner_cbs):
            cb(up)

    def writes(self):
        return [(i, m, a) for _, i, m, a in self.calls if m.startswith("set")]

    async def _maybe_hang(self):
        if self.hang:
            await asyncio.sleep(3600)

    def _dev(self, path):
        serial = path.rsplit("/", 1)[-1]
        if not self.owner or serial not in self.devices:
            raise OpenRazerError("org.freedesktop.DBus.Error.UnknownObject")
        return self.devices[serial]

    async def call(self, path, interface, member, signature="", args=()):
        self.calls.append((path, interface, member, tuple(args)))
        from razertux.openrazer import client
        try:
            await asyncio.wait_for(self._maybe_hang(), client.OPENRAZER_TIMEOUT)
        except asyncio.TimeoutError as e:
            raise OpenRazerError("timeout") from e
        if path == "/org/razer":
            if not self.owner:
                raise OpenRazerError("ServiceUnknown")
            return [list(self.devices)]
        dev = self._dev(path)
        if member not in dev.methods.get(interface, ()) or member in dev.fail:
            raise OpenRazerError(f"no method {interface}.{member}")
        if member == "getVidPid":
            return [[0x1532, dev.pid]]
        if member.startswith("set"):
            return []
        return [dev.values.get(member)]

    async def introspect(self, path):
        return {k: set(v) for k, v in self._dev(path).methods.items()}

    async def name_has_owner(self, name):
        return self.owner

    def on_owner_changed(self, name, cb):
        self._owner_cbs.append(cb)
