"""naga-configd: grabs the Naga, remaps via uinput, serves D-Bus.

Run in the foreground: `python -m naga_config.daemon.main [--debug]`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any

from naga_config.config.model import Profile, Settings, default_profile
from naga_config.config.store import ConfigStore
from naga_config.engine.mapper import Mapper
from naga_config.hw.buttons import DRIVER_MODE, NORMAL_MODE, parse_notification
from naga_config.backup import BackupError, load_backup, restore_backup, save_backup
from naga_config.razer.naga_v2_pro import ZONE_LED, NagaV2Pro
from naga_config.razer.transport import (
    HidrawTransport,
    RazerError,
    find_control_hidraw,
    find_vendor_hidraw,
)

from dbus_next import DBusError

from naga_config.i18n import tr

from .battery import BatteryMonitor, WarnState, notify_desktop
from .dbus_service import BUS_NAME, OBJECT_PATH, NagaService, openrazer_loaded
from .devices import DeviceWatcher, HidrawReader, InputRouter

logger = logging.getLogger("naga_config.daemon")

CONNECTION = {0x00A7: "wired", 0x00A8: "wireless"}
_DISCONNECTED = {"connected": False, "connection": "", "battery": -1, "charging": False,
                 "dpi": -1}
_HW_ERRORS = (RazerError, OSError, ValueError, IndexError)


def default_config_dir() -> Path:
    return Path.home() / ".config" / "naga-config"


def default_backup_path() -> Path:
    return default_config_dir() / "mouse-backup.json"


def load_scroll_stage(path: Path) -> int:
    """Last known scroll-wheel stage from state.json; -1 if unknown/unreadable."""
    try:
        stage = json.loads(Path(path).read_text(encoding="utf-8"))["scroll_stage"]
    except (OSError, ValueError, KeyError, TypeError):
        return -1
    return stage if isinstance(stage, int) else -1


def save_scroll_stage(path: Path, stage: int) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"scroll_stage": stage}) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def device_mode_for(profile: Profile | None) -> int:
    """Driver mode only when the profile remaps the top buttons; otherwise the
    firmware keeps them (front: scroll stage, back: DPI stage)."""
    return DRIVER_MODE if profile is not None and profile.remap_top_buttons else NORMAL_MODE


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
                 find_control: Callable[[tuple[int, ...]], str | None] = find_control_hidraw,
                 find_vendor: Callable[[tuple[int, ...]], str | None] = find_vendor_hidraw,
                 hidraw_reader: Callable[..., Any] = HidrawReader,
                 openrazer_loaded: Callable[[], bool] = openrazer_loaded,
                 apply_device_settings: Callable[[Profile], None] | None = None,
                 retry_delay: float = 0.2, backup_path: Path | None = None) -> None:
        self.out = out
        self._backup_path = backup_path or default_backup_path()
        self.notify: Callable[[str, str], None] | None = None  # desktop notification
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
                                   apply_settings=self._apply_power,
                                   restore=self._restore_backup,
                                   openrazer_loaded=openrazer_loaded,
                                   backup_path=self._backup_path)
        self._tasks: set[asyncio.Task] = set()
        self._mouse_path: str | None = None  # mouse node we talk to
        # every grabbed mouse node -> USB product (charge cable and dongle can both
        # be present; unplugging one falls back to the other)
        self._mice: dict[str, int] = {}
        self._transport: Any = None
        self._naga: NagaV2Pro | None = None
        self._vendor: Any = None
        self._hw = dict(_DISCONNECTED)
        self._mode: int | None = None  # device mode we last set (None: unknown)
        self._state_path = Path(store.root) / "state.json"
        self._scroll_stage = load_scroll_stage(self._state_path)
        self._absent_zones: set[str] = set()  # LEDs the mouse reported no brightness for
        # DPI stepping state, in memory only (reset on connect / profile switch)
        self._stages: list[tuple[int, int]] = []
        self._stage_idx = 0
        # backup fields of persistent settings we changed on the mouse (R17)
        self._written: set[str] = set()
        self._power_applied: dict[str, int] = {}  # skip rewriting unchanged values
        self._battery: BatteryMonitor | None = None
        self._poll_failed = False  # last wireless poll got no answer (mouse asleep?)
        self._battery_warn = WarnState()  # survives reconnects: one warning per discharge

    # -- Hardware protocol for the D-Bus service ---------------------------

    def hw_status(self) -> dict[str, Any]:
        return {**self._hw, "scroll_stage": self._scroll_stage}

    # -- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        self._warn_openrazer()
        await self.service.reload()

    def on_added(self, source: str, dev: Any) -> bool:
        """False if the device could not be grabbed (the watcher retries)."""
        if not self.router.add(source, dev):
            return False
        if source == "mouse":
            self._mice.pop(dev.path, None)
            self._mice[dev.path] = dev.info.product  # newest last
            self._mouse_path = dev.path
            self._spawn(self._connect(dev.info.product))
        return True

    def on_removed(self, path: str) -> None:
        self._spawn(self._removed(path))

    async def settle(self) -> None:
        """Wait for all background work (hotplug handling) to finish."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks))

    async def shutdown(self) -> None:
        await self.settle()
        self._write_back()
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
            if profile.poll_rate is not None:
                self._written.add("poll_rate")
            if profile.dpi_stages:
                self._written |= {"dpi_stages", "dpi"}
            apply_device_settings(self._naga, profile, self._absent_zones)

    def _apply_power(self, settings: Settings) -> None:
        """Idle time and the mouse's low power threshold (both persistent; None = keep)."""
        if self._naga is None:
            return
        for field, value, fn in (("idle_time", settings.idle_time, self._naga.set_idle_time),
                                 ("low_battery_threshold", settings.low_battery_threshold,
                                  self._naga.set_low_battery_threshold)):
            if value is None or self._power_applied.get(field) == value:
                continue
            self._written.add(field)
            self._power_applied[field] = value
            try:
                fn(value)
            except Exception as e:
                logger.warning("%s nicht gesetzt: %s", field, e)

    def _write_back(self) -> None:
        """R17: persistent values we changed go back to the backup's (VARSTORE), so
        other systems (Windows/Synapse) find the mouse as it was."""
        if self._naga is None or not self._written:
            return
        fields = set(self._written)
        if "dpi_stages" in fields:
            fields.add("dpi")  # writing stages resets the current DPI
        try:
            backup = load_backup(self._backup_path)
        except (OSError, ValueError) as e:
            logger.warning("Keine lesbare Sicherung (%s), %s bleiben in der Maus: %s",
                           self._backup_path, ", ".join(sorted(fields)), e)
            return
        failed = restore_backup(self._naga, backup, fields=fields)
        logger.info("Originalwerte zurückgeschrieben: %s%s", ", ".join(sorted(fields - set(failed))),
                    f" (fehlgeschlagen: {', '.join(failed)})" if failed else "")
        self._written.clear()

    def _sync_mode(self, profile: Profile | None) -> None:
        """Device mode follows the profile's remap_top_buttons (connect, switch, Reload)."""
        mode = device_mode_for(profile)
        if self._naga is None or mode == self._mode:
            return
        try:
            self._naga.set_device_mode(mode)
        except _HW_ERRORS as e:
            self._mode = None
            logger.warning("Gerätemodus %#04x nicht gesetzt: %s", mode, e)
            return
        self._mode = mode
        logger.info("Gerätemodus: %s", "Treiber (obere Tasten frei belegbar)"
                    if mode == DRIVER_MODE else "normal (obere Tasten ab Werk)")

    async def _vendor_report(self, report: bytes) -> None:
        """if01 hidraw: firmware notifications in both modes; top-button reports only
        count in driver mode (in normal mode the firmware handles those buttons)."""
        note = parse_notification(report)
        if note is not None:
            kind, value = note
            if kind == "scroll_stage":
                self._set_scroll_stage(value)
            elif self._hw["dpi"] != value:
                self._hw["dpi"] = value
                self.service.notify_status()
            return
        if self._mode == DRIVER_MODE:
            await self.router.vendor_report(report)

    def _set_scroll_stage(self, stage: int) -> None:
        if stage == self._scroll_stage:
            return
        self._scroll_stage = stage
        try:
            save_scroll_stage(self._state_path, stage)
        except OSError as e:
            logger.warning("Mausrad-Stufe nicht gespeichert (%s): %s", self._state_path, e)
        self.service.notify_status()

    def _apply_if_connected(self, profile: Profile) -> None:
        if self._naga is not None:
            self._sync_mode(profile)
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
        """DPI up/down button: step through the stages, clamped at both ends;
        cycle: next stage, wrapping from the last to the first."""
        if self._naga is None or not self._stages:
            logger.info("DPI-Stufe %s: keine Stufen bekannt oder Maus nicht verbunden", direction)
            return
        if direction == "cycle":
            idx = (self._stage_idx + 1) % len(self._stages)
        else:
            idx = min(max(self._stage_idx + (1 if direction == "up" else -1), 0),
                      len(self._stages) - 1)
        if idx == self._stage_idx:
            return
        x, y = self._stages[idx]
        self._written.add("dpi")
        try:
            self._naga.set_dpi(x, y)
        except Exception as e:
            logger.warning("DPI %dx%d nicht gesetzt: %s", x, y, e)
            return
        self._stage_idx = idx
        self._hw["dpi"] = x
        self.service.notify_status()
        logger.info("DPI-Stufe %d: %dx%d", idx + 1, x, y)

    def _restore_backup(self, backup: dict) -> list[str]:
        if self._naga is None:
            raise DBusError("io.github.nagaconfig.Error.NotConnected", "Maus nicht verbunden")
        failed = restore_backup(self._naga, backup)
        self._written.clear()
        self._power_applied = {}  # the mouse now holds the backup's values
        # DPI buttons should step through the restored onboard stages
        profile = Profile(id="", name="")
        self._reset_stages(profile)
        return failed

    async def _removed(self, path: str) -> None:
        await self.router.remove(path)
        self._mice.pop(path, None)
        if path == self._mouse_path:
            self._mouse_path = None
            await self._disconnect(reset_mode=False)  # unplug resets the mode itself
            if self._mice:  # e.g. charge cable pulled, dongle still connected
                self._mouse_path, product = list(self._mice.items())[-1]
                logger.info("Wechsle auf %s (%s)", self._mouse_path,
                            CONNECTION.get(product, f"{product:#06x}"))
                await self._connect(product)

    async def _connect(self, product: int) -> None:
        await self._disconnect(reset_mode=False)
        path = None
        for _ in range(10):  # hidraw nodes may appear after the input nodes
            path = self._find_control((product,))  # the node we were told about
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
        self._ensure_backup(naga)  # before anything persistent is written
        self._power_applied = {}
        self._poll_failed = False
        self._mode = None
        self._sync_mode(self.service.active_profile)
        vendor_path = self._find_vendor((product,))
        if vendor_path:
            reader = self._hidraw_reader(vendor_path, self._vendor_report)
            try:
                reader.start()
                self._vendor = reader
            except OSError as e:
                logger.warning("Kann %s nicht lesen: %s", vendor_path, e)
        self._absent_zones = {z for z, led in ZONE_LED.items()
                              if _safe(lambda led=led: naga.brightness(led), None) is None}
        self._hw = {"connected": True, "connection": CONNECTION.get(product, ""),
                    "battery": _safe(naga.battery, -1),
                    "charging": bool(_safe(naga.charging, False)), "dpi": -1}
        logger.info("Maus verbunden: %s", self._hw)
        self._warn_openrazer()
        self._apply_active()
        dpi = _safe(naga.dpi, None)
        self._hw["dpi"] = dpi[0] if dpi else -1
        self._apply_power(self.service.settings)
        if self._hw["connection"] == "wireless":
            self._battery = BatteryMonitor(
                naga, 60, self._on_battery, connection=lambda: self._hw["connection"],
                threshold=lambda: self.service.settings.low_battery_notify,
                notify=self._low_battery, state=self._battery_warn,
                on_poll=self._after_poll)
            self._battery.poll()
            self._battery.start()
        self.service.notify_status()

    def _ensure_backup(self, naga: NagaV2Pro) -> None:
        """One-time backup of the mouse's own values on the first connect (never
        overwrites an existing one), so the R17 write-back and Restore have a source."""
        if self._backup_path.exists():
            return
        try:
            save_backup(naga, self._backup_path)
        except (BackupError, OSError, ValueError) as e:
            logger.warning("Sicherung %s nicht angelegt: %s", self._backup_path, e)
            return
        logger.info("Ausgangszustand der Maus gesichert: %s", self._backup_path)

    def _after_poll(self, ok: bool) -> None:
        """Wireless poll: after sleep the mouse answers again but has dropped the device
        mode and the volatile (NOSTORE) settings. Detected by an answer after failed
        polls, or by a device mode other than the one we set; then re-apply both."""
        if not ok:
            self._poll_failed = True
            return
        woke, self._poll_failed = self._poll_failed, False
        naga = self._naga
        if naga is None:
            return
        if not woke:
            mode = _safe(naga.device_mode, None)
            if mode is None or mode == device_mode_for(self.service.active_profile):
                return
        logger.info("Maus wieder erreichbar – Gerätemodus und Profil werden neu gesetzt")
        self._mode = None
        self._sync_mode(self.service.active_profile)
        self._apply_active()
        dpi = _safe(naga.dpi, None)
        if dpi and self._hw["dpi"] != dpi[0]:
            self._hw["dpi"] = dpi[0]
            self.service.notify_status()

    def _on_battery(self, level: int, charging: bool) -> None:
        if (self._hw["battery"], self._hw["charging"]) != (level, charging):
            self._hw.update(battery=level, charging=charging)
            self.service.notify_status()

    def _low_battery(self, level: int) -> None:
        logger.warning("Akku schwach: %d %%", level)
        if self.notify is not None:
            try:
                self.notify(tr("daemon", "Naga V2 Pro: Akku schwach"),
                            tr("daemon", "Akkustand {level} % – bitte die Maus laden.", level=level))
            except Exception:
                logger.exception("Benachrichtigung nicht gesendet")

    async def _disconnect(self, reset_mode: bool) -> None:
        if self._battery is not None:
            await self._battery.stop()
            self._battery = None
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
        self._mode = None
        self._hw = dict(_DISCONNECTED)
        if was_connected:
            self.service.notify_status()


async def open_uinput(factory: Callable[[], Any], *, interval: float = 2.0,
                      timeout: float = 60.0,
                      sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> Any:
    """Open the virtual output device. Right after login /dev/uinput may not be usable
    yet (seat ACL not applied): retry every `interval` s for up to `timeout` s, then
    re-raise the last OSError."""
    waited = 0.0
    while True:
        try:
            return factory()
        except OSError as e:
            if waited >= timeout:
                raise
            if waited == 0:
                logger.warning("Kann /dev/uinput noch nicht öffnen (%s) – neuer Versuch alle "
                               "%g s, höchstens %g s lang", e, interval, timeout)
            await sleep(interval)
            waited += interval


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
        out = await open_uinput(UinputOutput)
    except OSError as e:
        logger.error("Kann /dev/uinput nicht öffnen (udev-Regel installiert?): %s", e)
        return 1
    daemon = Daemon(ConfigStore(config_dir), out)
    bus = None
    watcher = DeviceWatcher(daemon.on_added, daemon.on_removed)
    try:
        await daemon.start()
        bus = await MessageBus(bus_type=BusType.SESSION).connect()

        def notify(summary: str, body: str, bus: Any = bus) -> None:
            async def send() -> None:
                try:
                    await notify_desktop(bus, summary, body)
                except Exception as e:  # no notification daemon running
                    logger.warning("Benachrichtigung nicht gesendet: %s", e)
            daemon._spawn(send())
        daemon.notify = notify
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
