"""RoutedMouse: the RazerMouse surface, each feature group served by exactly one side.

A group is owned by OpenRazer when the matched OpenRazer device offers it and the model
is not listed in OPENRAZER_WRONG for it; otherwise RazerTux's own (hidraw) driver serves
it. Groups are never split: a lighting zone OpenRazer owns has no hidraw fallback per
effect, so two programs never write the same zone.
"""
from __future__ import annotations

from typing import Any, Literal

from ..razer import naga_v2_pro
from ..razer.mouse import RazerMouse
from ..razer.naga_v2_pro import NagaV2Pro
from .device import OpenRazerDevice, feature_group

GROUPS = ("dpi", "poll_rate", "power", "scroll", "lighting:logo", "lighting:scroll",
          "lighting:thumb", "lighting:backlight", "lighting:left", "lighting:right",
          "lighting:matrix")

# Groups kept on RazerTux although OpenRazer offers them. Naga V2 Pro DPI: RazerTux steps
# the verified onboard stage table (0x83) itself.
OPENRAZER_WRONG: dict[str, frozenset[str]] = {"naga_v2_pro": frozenset({"dpi"})}

# backup fields (razertux.backup) each group writes
GROUP_BACKUP_FIELDS = {"dpi": {"dpi", "dpi_stages"}, "poll_rate": {"poll_rate"},
                       "power": {"idle_time", "low_battery_threshold"},
                       "scroll": {"scroll_mode", "scroll_acceleration", "scroll_smart_reel"}}

Owner = Literal["openrazer", "razertux"]


class RoutedMouse:
    def __init__(self, local: RazerMouse, remote: OpenRazerDevice | None, model: str):
        self.local = local
        self.remote = remote
        self.model = model
        leds = naga_v2_pro.ZONE_LED if isinstance(local, NagaV2Pro) else {}
        self._zone_of = {led: zone for zone, led in leds.items()}

    def owner(self, group: str) -> Owner:
        if (self.remote is None or self.remote.failed
                or group in OPENRAZER_WRONG.get(self.model, ())):
            return "razertux"
        return "openrazer" if self.remote.offers(group) else "razertux"

    def owned_backup_fields(self) -> set[str]:
        return {f for g, fields in GROUP_BACKUP_FIELDS.items() if self.owner(g) == "openrazer"
                for f in fields}

    def _side(self, feature: str) -> Any:
        group = feature_group(feature)
        if group is not None and self.owner(group) == "openrazer":
            return self.remote
        return self.local

    def _zone(self, zone: str | int) -> str:
        return self._zone_of.get(zone, zone) if isinstance(zone, int) else zone

    def supports(self, feature: str) -> bool:
        return self._side(feature).supports(feature)

    # -- lighting (zone may be an LED id on the Naga) ------------------------------

    def set_effect(self, zone: str | int, lz: Any, **kw: Any) -> None:
        if self._side(f"set_effect:{self._zone(zone)}:x") is self.remote:
            self.remote.set_effect(self._zone(zone), lz)
        else:
            self.local.set_effect(zone, lz, **kw)

    def set_brightness(self, zone: str | int, value: int, **kw: Any) -> None:
        if self._side(f"set_brightness:{self._zone(zone)}") is self.remote:
            self.remote.set_brightness(self._zone(zone), value)
        else:
            self.local.set_brightness(zone, value, **kw)

    # -- everything else -----------------------------------------------------------

    def _route(self, feature: str, name: str) -> Any:
        return getattr(self._side(feature), name)

    def dpi(self) -> tuple[int, int]:
        return self._route("dpi", "dpi")()

    def battery(self) -> int:
        return self._route("battery", "battery")()

    def charging(self) -> bool:
        return self._route("charging", "charging")()

    def set_dpi(self, x: int, y: int, **kw: Any) -> None:
        self._route("set_dpi", "set_dpi")(x, y, **kw)

    def set_dpi_stages(self, active: int, stages: list[tuple[int, int]], **kw: Any) -> None:
        self._route("set_dpi_stages", "set_dpi_stages")(active, stages, **kw)

    def set_poll_rate(self, hz: int) -> None:
        self._route("set_poll_rate", "set_poll_rate")(hz)

    def set_idle_time(self, seconds: int) -> None:
        self._route("set_idle_time", "set_idle_time")(seconds)

    def set_low_battery_threshold(self, percent: int) -> None:
        self._route("set_low_battery_threshold", "set_low_battery_threshold")(percent)

    def set_scroll_mode(self, mode: int) -> None:
        self._route("set_scroll_mode", "set_scroll_mode")(mode)

    def set_scroll_acceleration(self, enabled: bool) -> None:
        self._route("set_scroll_acceleration", "set_scroll_acceleration")(enabled)

    def set_scroll_smart_reel(self, enabled: bool) -> None:
        self._route("set_scroll_smart_reel", "set_scroll_smart_reel")(enabled)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.local, name)
