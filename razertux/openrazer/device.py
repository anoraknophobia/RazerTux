"""One OpenRazer device behind RazerMouse-style methods.

Setters are synchronous (like RazerMouse) and queue the D-Bus call as a task; the tasks
run strictly in call order. Getters answer from a cache filled by ``refresh()``.
A failed or timed-out call logs once and calls ``on_failure`` once (the session then
falls back to hidraw); after that nothing more is sent.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from ..config.model import LightingZone
from ..razer.mouse import REACTIVE_SPEED, WAVE_DIRECTION, UnsupportedFeature
from .client import Bus, OpenRazerError, OrDevice

logger = logging.getLogger(__name__)

DPI = "razer.device.dpi"
MISC = "razer.device.misc"
POWER = "razer.device.power"
SCROLL = "razer.device.scroll"

# RazerTux zone -> (interface, method prefix, brightness interface, brightness prefix)
ZONES: dict[str, tuple[str, str, str, str]] = {
    "logo": ("razer.device.lighting.logo", "Logo", "razer.device.lighting.logo", "Logo"),
    "scroll": ("razer.device.lighting.scroll", "Scroll", "razer.device.lighting.scroll", "Scroll"),
    "backlight": ("razer.device.lighting.backlight", "Backlight",
                  "razer.device.lighting.backlight", "Backlight"),
    "left": ("razer.device.lighting.left", "Left", "razer.device.lighting.left", "Left"),
    "right": ("razer.device.lighting.right", "Right", "razer.device.lighting.right", "Right"),
    "matrix": ("razer.device.lighting.chroma", "", "razer.device.lighting.brightness", ""),
}

# LightingZone.effect -> (method suffix, needs colour)
EFFECTS = {"off": ("None", False), "static": ("Static", True), "breath": ("BreathSingle", True),
           "spectrum": ("Spectrum", False), "wave": ("Wave", False),
           "reactive": ("Reactive", True)}

# group -> (interface, methods that must all exist for OpenRazer to own the group)
_GROUP_METHODS = {
    "dpi": (DPI, ("getDPI", "setDPI", "getDPIStages", "setDPIStages")),
    "poll_rate": (MISC, ("getPollRate", "setPollRate")),
    "power": (POWER, ("getBattery", "isCharging")),
    "scroll": (SCROLL, ("setScrollMode", "setScrollAcceleration", "setScrollSmartReel")),
}

# RazerMouse feature -> (group, interface, OpenRazer method)
_FEATURES = {
    "dpi": ("dpi", DPI, "getDPI"), "set_dpi": ("dpi", DPI, "setDPI"),
    "dpi_stages": ("dpi", DPI, "getDPIStages"), "set_dpi_stages": ("dpi", DPI, "setDPIStages"),
    "poll_rate": ("poll_rate", MISC, "getPollRate"),
    "set_poll_rate": ("poll_rate", MISC, "setPollRate"),
    "battery": ("power", POWER, "getBattery"), "charging": ("power", POWER, "isCharging"),
    "idle_time": ("power", POWER, "getIdleTime"), "set_idle_time": ("power", POWER, "setIdleTime"),
    "low_battery_threshold": ("power", POWER, "getLowBatteryThreshold"),
    "set_low_battery_threshold": ("power", POWER, "setLowBatteryThreshold"),
    "set_scroll_mode": ("scroll", SCROLL, "setScrollMode"),
    "set_scroll_acceleration": ("scroll", SCROLL, "setScrollAcceleration"),
    "set_scroll_smart_reel": ("scroll", SCROLL, "setScrollSmartReel"),
}


def feature_group(feature: str) -> str | None:
    """Group a RazerMouse feature string belongs to (None: not routable)."""
    parts = feature.split(":")
    if parts[0] in ("brightness", "set_brightness", "set_effect") and len(parts) >= 2:
        return f"lighting:{parts[1]}"
    entry = _FEATURES.get(feature)
    return entry[0] if entry else None


def _pct(raw: int) -> float:
    return float(round(raw * 100 / 255))


class OpenRazerDevice:
    def __init__(self, bus: Bus, dev: OrDevice, on_failure: Callable[[], None]):
        self.bus = bus
        self.dev = dev
        self._on_failure = on_failure
        self.failed = False
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._cache: dict[str, Any] = {}

    # -- capabilities ------------------------------------------------------------

    def offers(self, group: str) -> bool:
        if group.startswith("lighting:"):
            zone = ZONES.get(group.split(":", 1)[1])
            if zone is None:
                return False
            iface, prefix, b_iface, b_prefix = zone
            return (self.dev.has(iface, f"set{prefix}None", f"set{prefix}Static")
                    and self.dev.has(b_iface, f"set{b_prefix}Brightness"))
        entry = _GROUP_METHODS.get(group)
        return entry is not None and self.dev.has(entry[0], *entry[1])

    def supports(self, feature: str) -> bool:
        parts = feature.split(":")
        if parts[0] == "set_effect" and len(parts) == 3 and parts[1] in ZONES:
            iface, prefix, _, _ = ZONES[parts[1]]
            effect = EFFECTS.get(parts[2])
            return effect is not None and self.dev.has(iface, f"set{prefix}{effect[0]}")
        if parts[0] in ("brightness", "set_brightness") and len(parts) == 2 and parts[1] in ZONES:
            _, _, b_iface, b_prefix = ZONES[parts[1]]
            kind = "get" if parts[0] == "brightness" else "set"
            return self.dev.has(b_iface, f"{kind}{b_prefix}Brightness")
        entry = _FEATURES.get(feature)
        return entry is not None and self.dev.has(entry[1], entry[2])

    # -- calls -------------------------------------------------------------------

    def _report(self, what: str, e: Exception) -> None:
        if not self.failed:
            self.failed = True
            logger.warning("OpenRazer: %s failed (%s) - RazerTux takes over", what, e)
            self._on_failure()

    def _queue(self, interface: str, member: str, signature: str, *args: Any) -> None:
        async def run() -> None:
            async with self._lock:
                if self.failed:
                    return
                try:
                    await self.bus.call(self.dev.path, interface, member, signature, args)
                except OpenRazerError as e:
                    self._report(f"{member}", e)

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @property
    def busy(self) -> bool:
        return bool(self._tasks)

    async def drain(self) -> None:
        """Wait until every queued write has been sent (or dropped)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def refresh(self) -> None:
        """Read battery, charging and DPI (what the device offers) into the cache."""
        for key, iface, member in (("battery", POWER, "getBattery"),
                                   ("charging", POWER, "isCharging"),
                                   ("dpi", DPI, "getDPI")):
            if not self.dev.has(iface, member):
                continue
            try:
                value, = await self.bus.call(self.dev.path, iface, member)
            except OpenRazerError as e:
                self._report(member, e)
                return
            self._cache[key] = value

    # -- getters (cache) ---------------------------------------------------------

    def _cached(self, key: str) -> Any:
        if key not in self._cache:
            raise OpenRazerError(f"{key} not read yet")
        return self._cache[key]

    def battery(self) -> int:
        return round(float(self._cached("battery")) * 255 / 100)

    def charging(self) -> bool:
        return bool(self._cached("charging"))

    def dpi(self) -> tuple[int, int]:
        x, y = self._cached("dpi")
        return int(x), int(y)

    # -- setters -----------------------------------------------------------------

    def set_dpi(self, x: int, y: int, *, store: int = 0) -> None:
        self._queue(DPI, "setDPI", "qq", x, y)

    def set_dpi_stages(self, active: int, stages: list[tuple[int, int]], *, store: int = 0) -> None:
        self._queue(DPI, "setDPIStages", "ya(qq)", active, [list(s) for s in stages])

    def set_poll_rate(self, hz: int) -> None:
        self._queue(MISC, "setPollRate", "q", hz)

    def set_idle_time(self, seconds: int) -> None:
        self._queue(POWER, "setIdleTime", "q", seconds)

    def set_low_battery_threshold(self, percent: int) -> None:
        self._queue(POWER, "setLowBatteryThreshold", "y", percent)

    def set_scroll_mode(self, mode: int) -> None:
        self._queue(SCROLL, "setScrollMode", "i", mode)

    def set_scroll_acceleration(self, enabled: bool) -> None:
        self._queue(SCROLL, "setScrollAcceleration", "b", bool(enabled))

    def set_scroll_smart_reel(self, enabled: bool) -> None:
        self._queue(SCROLL, "setScrollSmartReel", "b", bool(enabled))

    def set_brightness(self, zone: str, value: int, *, store: int = 0) -> None:
        _, _, b_iface, b_prefix = ZONES[zone]
        self._queue(b_iface, f"set{b_prefix}Brightness", "d", _pct(value))

    def set_effect(self, zone: str, lz: LightingZone, *, store: int = 0) -> None:
        if not self.supports(f"set_effect:{zone}:{lz.effect}"):
            raise UnsupportedFeature(f"OpenRazer: set_effect:{zone}:{lz.effect}")
        iface, prefix, _, _ = ZONES[zone]
        suffix, coloured = EFFECTS[lz.effect]
        member = f"set{prefix}{suffix}"
        r, g, b = lz.color
        if lz.effect == "reactive":
            self._queue(iface, member, "yyyy", r, g, b, REACTIVE_SPEED)
        elif lz.effect == "wave":
            self._queue(iface, member, "i", WAVE_DIRECTION)
        elif coloured:
            self._queue(iface, member, "yyy", r, g, b)
        else:
            self._queue(iface, member, "")
