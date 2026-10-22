"""naga-configd: grabs the Naga, remaps via uinput, serves D-Bus.

Run in the foreground: `python -m naga_config.daemon.main [--debug]`.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from naga_config.config.model import Profile, default_profile
from naga_config.config.store import ConfigStore
from naga_config.engine.mapper import Mapper
from naga_config.hw.buttons import DRIVER_MODE, NORMAL_MODE
from naga_config.backup import restore_backup
from naga_config.razer.naga_v2_pro import ZONE_LED, NagaV2Pro
from naga_config.razer.transport import (
    HidrawTransport,
    RazerError,
    find_control_hidraw,
    find_vendor_hidraw,
)

from dbus_next import DBusError

from .dbus_service import BUS_NAME, OBJECT_PATH, NagaService, openrazer_loaded
from .devices import DeviceWatcher, HidrawReader, InputRouter

logger = logging.getLogger("naga_config.daemon")

CONNECTION = {0x00A7: "wired", 0x00A8: "wireless"}
_DISCONNECTED = {"connected": False, "connection": "", "battery": -1, "charging": False}
_HW_ERRORS = (RazerError, OSError, ValueError, IndexError)


def default_config_dir() -> Path:
    return Path.home() / ".config" / "naga-config"


def _stage_index(profile: Profile) -> int:
    """0-based index of the profile's active DPI stage (profile.active_stage is
    1-based, 0/out of range clamps)."""
    return min(max(profile.active_stage - 1, 0), len(profile.dpi_stages) - 1)


def apply_device_settings(dev: NagaV2Pro, profile: Profile,
                          absent_zones: frozenset[str] | set[str] = frozenset()) -> None:
    """Write the profile's hardware settings to the mouse (all NOSTORE).

    Fixed order: poll rate, DPI stages (+ current DPI = active stage), then per
    lighting zone effect and brightness. Empty/None values never touch the mouse.
    Zones in absent_zones (LED not present) are skipped. Every command is guarded
    individually: a failure is logged and the next one still runs."""

    def run(what: str, fn: Callable[..., Any], *args: Any) -> None:
        try:
            fn(*args)
        except Exception as e:  # HW errors, but also ValueError from bad profile data
            logger.warning("%s nicht gesetzt: %s", what, e)

    if profile.poll_rate is not None:
        run("Abfragerate", dev.set_poll_rate, profile.poll_rate)
    if profile.dpi_stages:
        idx = _stage_index(profile)
        stages = [tuple(s) for s in profile.dpi_stages]
        run("DPI-Stufen", dev.set_dpi_stages, idx + 1, stages)
        run("DPI", dev.set_dpi, *stages[idx])
    for zone, led in ZONE_LED.items():
        cfg = profile.lighting.get(zone)
        if cfg is None or zone in absent_zones:
            continue
        run(f"Licht {zone}", dev.set_effect, led, cfg)
        run(f"Helligkeit {zone}", dev.set_brightness, led, cfg.brightness)


def _safe(fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except _HW_ERRORS as e:
        logger.debug("Abfrage fehlgeschlagen: %s", e)
        return default


class Daemon:
    """Wires devices, mapper, mouse control channel and D-Bus service together."""

    def __init__(self, store: ConfigStore, out: Any, *,
                 transport_factory: Callable[[str], Any] = HidrawTransport,
                 find_control: Callable[[], str | None] = find_control_hidraw,
                 find_vendor: Callable[[], str | None] = find_vendor_hidraw,
                 hidraw_reader: Callable[..., Any] = HidrawReader,
                 openrazer_loaded: Callable[[], bool] = openrazer_loaded,
                 apply_device_settings: Callable[[Profile], None] | None = None,
                 retry_delay: float = 0.2, backup_path: Path | None = None) -> None:
        self.out = out
        self._apply = apply_device_settings or self._apply_to_mouse
        self._openrazer = openrazer_loaded
        self._transport_factory = transport_factory
        self._find_control = find_control
        self._find_vendor = find_vendor
        self._hidraw_reader = hidraw_reader
        self._retry_delay = retry_delay
        self.mapper = Mapper(default_profile(), {}, out, on_dpi_stage=self._on_dpi_stage)
        self.router = InputRouter(self.mapper, out)
        self.service = NagaService(store, self.mapper, self,
                                   apply_device_settings=self._apply_if_connected,
                                   restore=self._restore_backup,
                                   openrazer_loaded=openrazer_loaded,
                                   backup_path=backup_path)
        self._tasks: set[asyncio.Task] = set()
        self._mouse_path: str | None = None
        self._transport: Any = None
        self._naga: NagaV2Pro | None = None
        self._vendor: Any = None
        self._hw = dict(_DISCONNECTED)
        self._absent_zones: set[str] = set()  # LEDs the mouse reported no brightness for
        # DPI stepping state, in memory only (reset on connect / profile switch)
        self._stages: list[tuple[int, int]] = []
        self._stage_idx = 0

    # -- Hardware protocol for the D-Bus service ---------------------------

    def hw_status(self) -> dict[str, Any]:
        return dict(self._hw)

    def scroll_profile_supported(self) -> bool:
        # R16: 0x02/0x98 is suspected of altering wheel haptics; never probe the
        # device until Task 11 has researched it.
        return False

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        self._warn_openrazer()
        await self.service.reload()

    def on_added(self, source: str, dev: Any) -> None:
        self.router.add(source, dev)
        if source == "mouse" and dev.path in self.router.paths:
            self._mouse_path = dev.path
            self._spawn(self._connect(dev.info.product))

    def on_removed(self, path: str) -> None:
        self._spawn(self._removed(path))

    async def settle(self) -> None:
        """Wait for all background work (hotplug handling) to finish."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks))

    async def shutdown(self) -> None:
        await self.settle()
        await self._disconnect(reset_mode=True)
        await self.router.close()
        self.out.close()

    # -- internals -------------------------------------------------------------

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        async def guarded() -> None:
            try:
                await coro
            except Exception:
                logger.exception("Hintergrundaufgabe fehlgeschlagen")

        task = asyncio.get_running_loop().create_task(guarded())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _warn_openrazer(self) -> None:
        if self._openrazer():
            logger.warning("OpenRazer-Treiber (razermouse) ist geladen und kann mit "
                           "naga-config kollidieren – bitte entladen oder deaktivieren")

    def _apply_to_mouse(self, profile: Profile) -> None:
        if self._naga is not None:
            apply_device_settings(self._naga, profile, self._absent_zones)

    def _apply_if_connected(self, profile: Profile) -> None:
        if self._naga is not None:
            self._reset_stages(profile)
            self._apply(profile)  # NagaService._use logs exceptions

    def _apply_active(self) -> None:
        profile = self.service.active_profile
        if profile is None:
            return
        try:
            self._reset_stages(profile)
            self._apply(profile)
        except Exception:
            logger.exception("Geräteeinstellungen für %s nicht angewendet", profile.id)

    def _reset_stages(self, profile: Profile) -> None:
        """Stage list to step through: the profile's, else the mouse's onboard stages."""
        if profile.dpi_stages:
            self._stages = [tuple(s) for s in profile.dpi_stages]
            self._stage_idx = _stage_index(profile)
            return
        self._stages, self._stage_idx = [], 0
        if self._naga is None:
            return
        onboard = _safe(self._naga.dpi_stages, None)
        if onboard and onboard[1]:
            active, stages = onboard
            self._stages = [tuple(s) for s in stages]
            self._stage_idx = min(max(active - 1, 0), len(stages) - 1)

    def _on_dpi_stage(self, direction: str) -> None:
        """DPI up/down button: step through the stages, clamped at both ends."""
        if self._naga is None or not self._stages:
            logger.info("DPI-Stufe %s: keine Stufen bekannt oder Maus nicht verbunden", direction)
            return
        idx = min(max(self._stage_idx + (1 if direction == "up" else -1), 0),
                  len(self._stages) - 1)
        if idx == self._stage_idx:
            return
        x, y = self._stages[idx]
        try:
            self._naga.set_dpi(x, y)
        except Exception as e:
            logger.warning("DPI %dx%d nicht gesetzt: %s", x, y, e)
            return
        self._stage_idx = idx
        logger.info("DPI-Stufe %d: %dx%d", idx + 1, x, y)

    def _restore_backup(self, backup: dict) -> list[str]:
        if self._naga is None:
            raise DBusError("io.github.nagaconfig.Error.NotConnected", "Maus nicht verbunden")
        failed = restore_backup(self._naga, backup)
        # DPI buttons should step through the restored onboard stages
        profile = Profile(id="", name="")
        self._reset_stages(profile)
        return failed

    async def _removed(self, path: str) -> None:
        await self.router.remove(path)
        if path == self._mouse_path:
            self._mouse_path = None
            await self._disconnect(reset_mode=False)  # unplug resets the mode itself

    async def _connect(self, product: int) -> None:
        await self._disconnect(reset_mode=False)
        path = None
        for _ in range(10):  # hidraw nodes may appear after the input nodes
            path = self._find_control()
            if path:
                break
            await asyncio.sleep(self._retry_delay)
        if not path:
            logger.warning("Steuerkanal (hidraw) der Maus nicht gefunden")
            return
        try:
            self._transport = self._transport_factory(path)
        except OSError as e:
            logger.warning("Kann %s nicht öffnen: %s", path, e)
            return
        naga = self._naga = NagaV2Pro(self._transport)
        try:
            naga.set_device_mode(DRIVER_MODE)
        except _HW_ERRORS as e:
            logger.warning("Treibermodus nicht gesetzt, obere Tasten bleiben ab Werk: %s", e)
        vendor_path = self._find_vendor()
        if vendor_path:
            reader = self._hidraw_reader(vendor_path, self.router.vendor_report)
            try:
                reader.start()
                self._vendor = reader
            except OSError as e:
                logger.warning("Kann %s nicht lesen: %s", vendor_path, e)
        self._absent_zones = {z for z, led in ZONE_LED.items()
                              if _safe(lambda led=led: naga.brightness(led), None) is None}
        self._hw = {"connected": True, "connection": CONNECTION.get(product, ""),
                    "battery": _safe(naga.battery, -1),
                    "charging": bool(_safe(naga.charging, False))}
        logger.info("Maus verbunden: %s", self._hw)
        self._warn_openrazer()
        self._apply_active()
        self.service.notify_status()

    async def _disconnect(self, reset_mode: bool) -> None:
        if self._vendor is not None:
            await self._vendor.close()
            self._vendor = None
        if self._naga is not None and reset_mode:
            try:
                self._naga.set_device_mode(NORMAL_MODE)
            except _HW_ERRORS as e:
                logger.warning("Normalmodus nicht zurückgesetzt: %s", e)
        if self._transport is not None:
            close = getattr(self._transport, "close", None)
            if close:
                close()
        was_connected = self._hw["connected"]
        self._transport = self._naga = None
        self._hw = dict(_DISCONNECTED)
        if was_connected:
            self.service.notify_status()


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="naga-configd",
                                 description="naga-config Dienst (Razer Naga V2 Pro)")
    ap.add_argument("--debug", action="store_true", help="ausführliches Protokoll")
    ap.add_argument("--config-dir", type=Path, default=None,
                    help="Konfigurationsordner (Standard: ~/.config/naga-config)")
    return ap


async def run(config_dir: Path) -> int:
    from dbus_next import BusType
    from dbus_next.aio import MessageBus
    from dbus_next.constants import NameFlag, RequestNameReply

    from .uinput_out import UinputOutput

    try:
        out = UinputOutput()
    except OSError as e:
        logger.error("Kann /dev/uinput nicht öffnen (udev-Regel installiert?): %s", e)
        return 1
    daemon = Daemon(ConfigStore(config_dir), out)
    bus = None
    watcher = DeviceWatcher(daemon.on_added, daemon.on_removed)
    try:
        await daemon.start()
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
        bus.export(OBJECT_PATH, daemon.service)
        reply = await bus.request_name(BUS_NAME, NameFlag.DO_NOT_QUEUE)
        if reply != RequestNameReply.PRIMARY_OWNER:
            logger.error("%s ist bereits belegt – läuft der Dienst schon?", BUS_NAME)
            return 1
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        watcher.start()  # monitor first, then scan: nothing is missed
        watcher.scan()
        logger.info("naga-configd läuft")
        await stop.wait()
        logger.info("Beende …")
        return 0
    finally:
        watcher.stop()
        await daemon.shutdown()
        if bus is not None:
            bus.disconnect()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(stream=sys.stderr,
                        level=logging.DEBUG if args.debug else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(run(args.config_dir or default_config_dir()))


if __name__ == "__main__":
    sys.exit(main())
