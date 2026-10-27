"""One connected mouse model: driver, input routing, mapper, profile, probe, status.

The daemon keeps one DeviceSession per device model (cable and dongle of the same
model share it; the node connected last is the one we talk to). Everything
the Naga V2 Pro needs beyond the generic driver (device mode, top buttons and firmware
notifications on the if01 hidraw node, scroll-wheel stage, invalid-DPI repair) only runs
when the driver is a NagaV2Pro.

Write protection: every connect starts with the read-only probe. An
experimental (unverified) mouse whose probe fails gets no device setting at all (DPI,
lighting, poll rate, power) until it is reconnected; a verified model only logs a
warning and behaves as before.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from evdev import ecodes as E

from razertux.backup import BackupError, backup_path, load_backup, restore_backup, save_backup
from razertux.config.model import (
    DPI_MAX, DPI_MIN, NAGA_MODEL, DefaultAction, DeviceSettings, Profile, Settings, validate,
)
from razertux.config.store import ModelStore
from razertux.devices.db import DeviceInfo
from razertux.engine.mapper import NAGA_DEFAULT_DPI, Mapper
from razertux.hw.button_maps import InputSignal, map_for
from razertux.hw.buttons import DRIVER_MODE, NORMAL_MODE, parse_notification
from razertux.i18n import tr
from razertux.razer.mouse import RazerMouse, make_mouse
from razertux.razer.naga_v2_pro import ZONE_LED, NagaV2Pro
from razertux.razer.probe import ProbeResult, diagnostics, probe
from razertux.razer.transport import (
    HidrawTransport, RazerError, find_control_hidraw, find_vendor_hidraw,
)

from .battery import BatteryMonitor, WarnState
from .devices import HidrawReader, InputRouter

logger = logging.getLogger("razertux.daemon")

WIRELESS = ("wireless", "receiver")
_DISCONNECTED = {"connected": False, "connection": "", "battery": -1, "charging": False,
                 "dpi": -1}
_HW_ERRORS = (RazerError, OSError, ValueError, IndexError)


class NotConnected(Exception):
    """The session's mouse is not connected."""


class ReadOnly(Exception):
    """The session's mouse failed the probe: nothing is written to it."""


# -- helpers --------------------------------------------------------------------------

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


def _guarded(what: str, fn: Callable[..., Any], *args: Any) -> None:
    try:
        fn(*args)
    except Exception as e:  # HW errors, but also ValueError from bad profile data
        logger.warning("%s not set: %s", what, e)


def apply_device_settings(dev: NagaV2Pro, profile: Profile,
                          absent_zones: frozenset[str] | set[str] = frozenset()) -> None:
    """Write the profile's hardware settings to the Naga V2 Pro (all NOSTORE).

    Fixed order: poll rate, DPI stages (+ current DPI = active stage), then per
    lighting zone effect and brightness. Empty/None values never touch the mouse.
    Zones in absent_zones (LED not present) are skipped. Every command is guarded
    individually: a failure is logged and the next one still runs."""
    if profile.poll_rate is not None:
        _guarded("Polling rate", dev.set_poll_rate, profile.poll_rate)
    if profile.dpi_stages:
        idx = _stage_index(profile)
        stages = [tuple(s) for s in profile.dpi_stages]
        _guarded("DPI stages", dev.set_dpi_stages, idx + 1, stages)
        _guarded("DPI", dev.set_dpi, *stages[idx])
    for zone, led in ZONE_LED.items():
        cfg = profile.lighting.get(zone)
        if cfg is None or zone in absent_zones:
            continue
        _guarded(f"Lighting {zone}", dev.set_effect, led, cfg)
        _guarded(f"Brightness {zone}", dev.set_brightness, led, cfg.brightness)


def apply_generic_settings(dev: RazerMouse, profile: Profile) -> set[str]:
    """Same order as apply_device_settings, for any database mouse: only what
    dev.supports() is sent, the rest is skipped. Returns the persistent backup fields
    written (for the write-back)."""
    written: set[str] = set()
    if profile.poll_rate is not None and dev.supports("set_poll_rate"):
        written.add("poll_rate")
        _guarded("Polling rate", dev.set_poll_rate, profile.poll_rate)
    if profile.dpi_stages:
        idx = _stage_index(profile)
        stages = [tuple(s) for s in profile.dpi_stages]
        if dev.supports("set_dpi_stages"):
            written |= {"dpi_stages", "dpi"}
            _guarded("DPI stages", dev.set_dpi_stages, idx + 1, stages)
        if dev.supports("set_dpi"):
            written.add("dpi")
            _guarded("DPI", dev.set_dpi, *stages[idx])
    for zone, cfg in profile.lighting.items():
        if dev.supports(f"set_effect:{zone}:{cfg.effect}"):
            _guarded(f"Lighting {zone}", dev.set_effect, zone, cfg)
        else:
            logger.debug("Lighting %s/%s: not supported by %s", zone, cfg.effect,
                         dev.info.model)
        if dev.supports(f"set_brightness:{zone}"):
            _guarded(f"Brightness {zone}", dev.set_brightness, zone, cfg.brightness)
    for key, setter, convert in (("mode", "scroll_mode", int), ("acceleration", "scroll_acceleration", bool),
                                 ("smart_reel", "scroll_smart_reel", bool)):
        if key in profile.scroll and dev.supports(f"set_{setter}"):
            written.add(setter)  # persistent (VARSTORE): the write-back covers it
            _guarded(f"Scroll wheel {key}", getattr(dev, f"set_{setter}"), convert(profile.scroll[key]))
    return written


def _safe(fn: Callable[[], Any], default: Any) -> Any:
    try:
        return fn()
    except _HW_ERRORS as e:
        logger.debug("Query failed: %s", e)
        return default


def _remaps(profile: Profile) -> bool:
    """Any button bound to something other than its factory behaviour (incl. macro,
    Hypershift)."""
    return any(not isinstance(a, DefaultAction)
               for layer in (profile.standard, profile.hypershift) for a in layer.values())


def needs_grab(profile: Profile | None, device_settings: DeviceSettings,
               info: DeviceInfo) -> bool:
    """Grab the mouse's input nodes only when we must.

    Rule 1: a verified model (Naga V2 Pro) is always grabbed while connected,
    exactly as before multi-device support. Rule 2: an experimental mouse is grabbed
    only if the profile binds any button to something other than DefaultAction (macro
    and Hypershift included) and remapping is switched on for it."""
    if info.verified:
        return True
    return device_settings.remap_enabled and profile is not None and _remaps(profile)


# -- session --------------------------------------------------------------------------

class DeviceSession:
    """One device model: RazerMouse, InputRouter, Mapper, active profile, probe result.

    The session owns its model's active profile (Settings.active_profiles[model]):
    reload() on every connect reads the device settings, macros and that profile."""

    def __init__(self, info: DeviceInfo, store: ModelStore, out: Any, *,
                 transport_factory: Callable[[str], Any] = HidrawTransport,
                 find_control: Callable[[tuple[int, ...]], str | None] = find_control_hidraw,
                 find_vendor: Callable[[tuple[int, ...]], str | None] = find_vendor_hidraw,
                 hidraw_reader: Callable[..., Any] = HidrawReader,
                 retry_delay: float = 0.2,
                 apply_device_settings: Callable[[Profile], None] | None = None,
                 settings: Callable[[], Settings] | None = None,
                 on_status: Callable[[], None] | None = None,
                 on_learned: Callable[[InputSignal], None] | None = None,
                 notify: Callable[[str, str], None] | None = None,
                 warn_openrazer: Callable[[], None] | None = None) -> None:
        self.info = info
        self.model = info.model
        self.store = store
        self.out = out
        self.backup_path = backup_path(info.model, store.root)
        self.on_learned = on_learned or (lambda signal: None)
        self._transport_factory = transport_factory
        self._find_control = find_control
        self._find_vendor = find_vendor
        self._hidraw_reader = hidraw_reader
        self._retry_delay = retry_delay
        self._apply = apply_device_settings or self._apply_to_mouse
        self._settings_source = settings or store.config.load_settings
        self._settings_cache: Settings | None = None
        self._on_status_cb = on_status or (lambda: None)
        self._notify = notify or (lambda title, body: None)
        self._warn_openrazer = warn_openrazer or (lambda: None)
        self._tasks: set[asyncio.Task] = set()

        self.device_settings = store.load_device_settings()
        self.profile: Profile | None = None
        self._macros: dict = {}
        self.validation: list[str] = []  # problems of the active profile (macros, ...)
        self.mapper = Mapper(store.default_profile(), {}, out, on_dpi_stage=self._on_dpi_stage,
                             default_dpi=NAGA_DEFAULT_DPI if self._is_naga_model else {})
        self.router = InputRouter(self.mapper, out, grab=needs_grab(None, self.device_settings, info))
        self._refresh_map()
        self._learning = False
        self._grab_lock = asyncio.Lock()  # serialises grab changes (pause/resume/learning)
        self._paused = False  # device recorder: grabs released, mapping stopped
        # device recorder: the helper shares the control node, so while paused nothing is sent
        # on it (writes, mode, onboard stage reads, battery polls); True when something was
        # skipped and resume() must apply the active profile
        self._skipped_while_paused = False

        self._paths: set[str] = set()  # every input node of this model we were given
        self._mouse_path: str | None = None  # mouse node whose control channel we use
        # every mouse node -> USB product (charge cable and dongle can both be present;
        # unplugging one falls back to the other)
        self._mice: dict[str, int] = {}
        self._pid: int | None = None
        self._connect_gen = 0  # newest _connect run (older overlapping runs give up)
        self._transport: Any = None
        self._mouse: RazerMouse | None = None
        self.probe_result: ProbeResult | None = None
        self._write_ok = True  # False: experimental mouse failed the probe (read-only)
        self._vendor: Any = None
        self._hw = dict(_DISCONNECTED)
        self._mode: int | None = None  # device mode we last set (None: unknown)
        self._state_path = store.root / "state.json" if self._is_naga_model else None
        self._scroll_stage = load_scroll_stage(self._state_path) if self._state_path else -1
        self._absent_zones: set[str] = set()  # LEDs the mouse reported no brightness for
        # DPI stepping state, in memory only (reset on connect / profile switch)
        self._stages: list[tuple[int, int]] = []
        self._stage_idx = 0
        # backup fields of persistent settings we changed on the mouse
        self._written: set[str] = set()
        self._power_applied: dict[str, int] = {}  # skip rewriting unchanged values
        self._battery: BatteryMonitor | None = None
        self._poll_failed = False  # last wireless poll got no answer (mouse asleep?)
        self._battery_warn = WarnState()  # survives reconnects: one warning per discharge

    @property
    def _is_naga_model(self) -> bool:
        return self.model == NAGA_MODEL

    @property
    def _naga(self) -> NagaV2Pro | None:
        return self._mouse if isinstance(self._mouse, NagaV2Pro) else None

    @property
    def connected(self) -> bool:
        return self._mouse is not None

    @property
    def writable(self) -> bool:
        return self._mouse is not None and self._write_ok

    # -- inputs ------------------------------------------------------------------------

    def owns(self, path: str) -> bool:
        return path in self._paths

    def add_input(self, pid: int, source: str, dev: Any) -> bool:
        """False if the device could not be grabbed (the watcher retries)."""
        if not self.router.add(source, dev):
            return False
        self._paths.add(dev.path)
        if source == "mouse":
            self._mice.pop(dev.path, None)
            self._mice[dev.path] = pid  # newest last
            self._mouse_path = dev.path
            self._spawn(self._connect(pid))
        return True

    def remove_input(self, path: str) -> None:
        self._spawn(self._removed(path))

    async def settle(self) -> None:
        """Wait for all background work (hotplug handling) to finish."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks))

    async def close(self) -> None:
        """Clean shutdown: write-back, normal mode, release and ungrab."""
        await self.settle()
        self._paused = False
        self._write_back()
        await self._disconnect(reset_mode=True)
        await self.router.close()

    # -- profile ---------------------------------------------------------------------

    async def activate(self, profile_id: str) -> None:
        known = {p.id for p in self.store.list_profiles()} | {"default"}
        if profile_id not in known:
            raise KeyError(profile_id)
        profile = self.store.load_profile(profile_id)
        await self._use(profile)
        config = self.store.config
        settings = config.load_settings()
        settings.active_profiles[self.model] = profile.id
        config.save_settings(settings)
        self._on_status()

    async def reload(self) -> None:
        """Re-read device settings, macros, settings and the active profile; apply."""
        self.reload_device_settings()
        self._macros = self.store.config.load_macros()
        self.mapper.set_macros(self._macros)
        settings = self._settings_cache = self._settings_source()
        profile = self.store.load_profile(settings.active_profiles.get(self.model, "default"))
        await self._use(profile)
        self.apply_power(settings)
        self._on_status()

    def reload_device_settings(self) -> None:
        self.device_settings = self.store.load_device_settings()
        self._refresh_map()

    @property
    def active_profile_id(self) -> str:
        if self.profile is not None:
            return self.profile.id
        return self._settings_source().active_profiles.get(self.model, "default")

    def config_errors(self) -> list[str]:
        return list(dict.fromkeys([*self.store.errors, *self.validation]))

    def _take(self, profile: Profile) -> None:
        self.profile = profile
        if self._mouse is not None:
            self._sync_mode(profile)
            self._reset_stages(profile)
            self._apply_profile(profile)

    async def _use(self, profile: Profile) -> None:
        await self.mapper.set_profile(profile)
        self.validation = validate(profile, self._macros)
        try:
            self._take(profile)
        except Exception:
            logger.exception("Device settings for %s not applied", profile.id)
        await self._update_grab()

    def _refresh_map(self) -> None:
        learned = self.device_settings.learned
        self.router.set_map(map_for(self.info, learned, self.device_settings.plate))
        self.mapper.set_defaults({b: s.code for b, s in learned.items()
                                  if s.ev_type == E.EV_KEY})  # the key it sends

    def _wants_grab(self, profile: Profile | None = None) -> bool:
        if self._paused:
            return False
        profile = self.profile if profile is None else profile
        return self._learning or needs_grab(profile, self.device_settings, self.info)

    async def _update_grab(self) -> None:
        async with self._grab_lock:
            while True:  # state may change while set_grab awaits: settle on the latest
                want = self._wants_grab()
                if want == self.router.grabbed:
                    return
                logger.info("%s: input devices %s", self.info.name,
                            "grabbed" if want else "released")
                await self.router.set_grab(want)

    # -- pause (device recorder) ---------------------------------------------------------

    async def pause(self) -> None:
        """Release every grab and stop mapping, so a recorder can read the raw device.
        Learning ends. Profile switches and reloads keep the nodes released until
        resume(). Idempotent."""
        if self._paused:
            return
        self._paused = True
        self.router.learn(None)
        self._learning = False
        await self._update_grab()
        self._on_status()

    async def resume(self) -> None:
        """Back to the grab state the active profile asks for, then apply what was held back
        while paused. Idempotent. If re-grabbing fails the session stays paused (the caller
        retries)."""
        if not self._paused:
            return
        self._paused = False
        try:
            await self._update_grab()
        except BaseException:
            self._paused = True
            raise
        if self._skipped_while_paused and self._mouse is not None:
            self._skipped_while_paused = False
            self._sync_mode(self.profile)
            self._apply_active()
            self.apply_power(self._settings())
        self._on_status()

    @property
    def paused(self) -> bool:
        return self._paused

    # -- learning --------------------------------------------------------------------

    async def start_learning(self) -> None:
        """Report the next key press the button map does not know via on_learned
        (it is not output). The nodes are grabbed meanwhile."""
        self._learning = True
        self.router.learn(self._learned)
        await self._update_grab()

    async def stop_learning(self) -> None:
        self.router.learn(None)
        self._learning = False
        await self._update_grab()

    async def _learned(self, signal: InputSignal) -> None:
        self._learning = False
        await self._update_grab()  # back to the grab state before learning
        try:
            self.on_learned(signal)
        except Exception:
            logger.exception("Learning feedback failed")

    # -- status ----------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {**self._hw, "scroll_stage": self._scroll_stage,
                "model": self.model, "name": self.info.name,
                "verified": self.info.verified, "writable": self.writable,
                "probe_ok": None if self.probe_result is None else self.probe_result.ok,
                "grabbed": self.router.grabbed, "learning": self._learning,
                "paused": self._paused}

    def diagnostics(self) -> str:
        if self.probe_result is None or self._pid is None:
            return ""
        return diagnostics(self.info, self._pid, self.probe_result, self.probe_result.raw)

    def _on_status(self) -> None:
        self._on_status_cb()

    # -- internals -------------------------------------------------------------------

    def _spawn(self, coro: Coroutine[Any, Any, None]) -> None:
        async def guarded() -> None:
            try:
                await coro
            except Exception:
                logger.exception("Background task failed")

        task = asyncio.get_running_loop().create_task(guarded())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _settings(self) -> Settings:
        if self._settings_cache is None:
            self._settings_cache = self._settings_source()
        return self._settings_cache

    def _can(self, feature: str) -> bool:
        """The Naga driver keeps its own (verified) commands; others ask the database."""
        m = self._mouse
        return m is not None and (isinstance(m, NagaV2Pro) or m.supports(feature))

    def _writes_allowed(self) -> bool:
        """False (nothing is written) when not connected, paused for the device recorder
        (applied on resume) or read-only after a failed probe; that case was logged once
        at connect."""
        if self._mouse is None:
            return False
        if self._paused:
            self._skipped_while_paused = True
            return False
        if not self._write_ok:
            logger.debug("%s write-protected: setting discarded", self.info.name)
            return False
        return True

    def _apply_profile(self, profile: Profile) -> None:
        if self._writes_allowed():
            self._apply(profile)

    def _apply_to_mouse(self, profile: Profile) -> None:
        naga = self._naga
        if naga is not None:
            if profile.poll_rate is not None:
                self._written.add("poll_rate")
            if profile.dpi_stages:
                self._written |= {"dpi_stages", "dpi"}
            apply_device_settings(naga, profile, self._absent_zones)
        elif self._mouse is not None:
            self._written |= apply_generic_settings(self._mouse, profile)

    def apply_power(self, settings: Settings) -> None:
        """Idle time and the mouse's low power threshold (both persistent; None = keep).
        These settings are global, so only verified mice (the Naga) get them;
        experimental mice never do."""
        m = self._mouse
        if m is None or not self.info.verified:
            return
        for field, value in (("idle_time", settings.idle_time),
                             ("low_battery_threshold", settings.low_battery_threshold)):
            if value is None or self._power_applied.get(field) == value:
                continue
            if not self._can(f"set_{field}") or not self._writes_allowed():
                continue
            self._written.add(field)
            self._power_applied[field] = value
            try:
                getattr(m, f"set_{field}")(value)
            except Exception as e:
                logger.warning("%s not set: %s", field, e)

    def _write_back(self) -> None:
        """Write-back: persistent values we changed go back to the backup's (VARSTORE), so
        other systems (Windows/Synapse) find the mouse as it was."""
        if self._mouse is None or not self._write_ok or not self._written:
            return
        fields = set(self._written)
        if "dpi_stages" in fields:
            fields.add("dpi")  # writing stages resets the current DPI
        try:
            backup = load_backup(self.backup_path)
        except (OSError, ValueError) as e:
            logger.warning("No readable backup (%s), %s stay in the mouse: %s",
                           self.backup_path, ", ".join(sorted(fields)), e)
            return
        failed = restore_backup(self._mouse, backup, fields=fields)
        logger.info("Original values written back: %s%s", ", ".join(sorted(fields - set(failed))),
                    f" (failed: {', '.join(failed)})" if failed else "")
        self._written.clear()

    def _sync_mode(self, profile: Profile | None) -> None:
        """Naga: device mode follows the profile's remap_top_buttons (connect, switch,
        Reload)."""
        naga = self._naga
        mode = device_mode_for(profile)
        if naga is None or mode == self._mode:
            return
        if self._paused:
            self._skipped_while_paused = True
            return
        try:
            naga.set_device_mode(mode)
        except _HW_ERRORS as e:
            self._mode = None
            logger.warning("Device mode %#04x not set: %s", mode, e)
            return
        self._mode = mode
        logger.info("Device mode: %s", "driver (top buttons freely assignable)"
                    if mode == DRIVER_MODE else "normal (top buttons as shipped)")

    async def _vendor_report(self, report: bytes) -> None:
        """Naga if01 hidraw: firmware notifications in both modes; top-button reports
        only count in driver mode (in normal mode the firmware handles those buttons)."""
        note = parse_notification(report)
        if note is not None:
            kind, value = note
            if kind == "scroll_stage":
                self._set_scroll_stage(value)
            elif not DPI_MIN <= value <= DPI_MAX:
                self._fix_invalid_dpi(value)
            elif self._hw["dpi"] != value:
                self._hw["dpi"] = value
                self._on_status()
            return
        if self._mode == DRIVER_MODE:
            await self.router.vendor_report(report)

    def _fix_invalid_dpi(self, bad: int) -> None:
        """The Naga reported a DPI beyond its range (an invalid onboard stage):
        re-activate a valid stage and set that stage's DPI."""
        logger.warning("Mouse reports invalid DPI %d (allowed %d-%d) - correcting",
                       bad, DPI_MIN, DPI_MAX)
        naga = self._naga
        if naga is None or not self._writes_allowed():
            return
        xy = _safe(naga.repair_active_stage, None)
        if xy is None:
            last = self._hw["dpi"]
            if not DPI_MIN <= last <= DPI_MAX:
                logger.warning("No valid DPI known - please set the DPI in the Performance tab")
                return
            xy = (last, last)
        try:
            naga.set_dpi(*xy)
        except _HW_ERRORS as e:
            logger.warning("DPI %dx%d not set: %s", xy[0], xy[1], e)
            return
        self._hw["dpi"] = xy[0]
        self._on_status()

    def _set_scroll_stage(self, stage: int) -> None:
        if stage == self._scroll_stage or self._state_path is None:
            return
        self._scroll_stage = stage
        try:
            save_scroll_stage(self._state_path, stage)
        except OSError as e:
            logger.warning("Scroll wheel stage not saved (%s): %s", self._state_path, e)
        self._on_status()

    def _apply_active(self) -> None:
        profile = self.profile
        if profile is None:
            return
        try:
            self._reset_stages(profile)
            self._apply_profile(profile)
        except Exception:
            logger.exception("Device settings for %s not applied", profile.id)

    def _reset_stages(self, profile: Profile) -> None:
        """Stage list to step through: the profile's, else the mouse's onboard stages."""
        if profile.dpi_stages:
            self._stages = [tuple(s) for s in profile.dpi_stages]
            self._stage_idx = _stage_index(profile)
            return
        self._stages, self._stage_idx = [], 0
        if self._mouse is None or not self._can("dpi_stages"):
            return
        if self._paused:
            self._skipped_while_paused = True
            return
        onboard = _safe(self._mouse.dpi_stages, None)
        if onboard and onboard[1]:
            active, stages = onboard
            self._stages = [tuple(s) for s in stages]
            self._stage_idx = min(max(active - 1, 0), len(stages) - 1)

    def _on_dpi_stage(self, direction: str) -> None:
        """DPI up/down button: step through the stages, clamped at both ends;
        cycle: next stage, wrapping from the last to the first."""
        if self._mouse is None or not self._stages or not self._can("set_dpi"):
            logger.info("DPI stage %s: no stages known or mouse not connected", direction)
            return
        if direction == "cycle":
            idx = (self._stage_idx + 1) % len(self._stages)
        else:
            idx = min(max(self._stage_idx + (1 if direction == "up" else -1), 0),
                      len(self._stages) - 1)
        if idx == self._stage_idx or not self._writes_allowed():
            return
        x, y = self._stages[idx]
        self._written.add("dpi")
        try:
            self._mouse.set_dpi(x, y)
        except Exception as e:
            logger.warning("DPI %dx%d not set: %s", x, y, e)
            return
        self._stage_idx = idx
        self._hw["dpi"] = x
        self._on_status()
        logger.info("DPI stage %d: %dx%d", idx + 1, x, y)

    def restore_backup(self, backup: dict) -> list[str]:
        if self._mouse is None:
            raise NotConnected(self.model)
        if not self._write_ok:
            raise ReadOnly(self.model)
        failed = restore_backup(self._mouse, backup)
        self._written.clear()
        self._power_applied = {}  # the mouse now holds the backup's values
        # DPI buttons should step through the restored onboard stages
        self._reset_stages(Profile(id="", name=""))
        return failed

    async def _removed(self, path: str) -> None:
        await self.router.remove(path)
        self._paths.discard(path)
        self._mice.pop(path, None)
        if path == self._mouse_path:
            self._mouse_path = None
            self._connect_gen += 1  # a connect still waiting for this node gives up
            await self._disconnect(reset_mode=False)  # unplug resets the mode itself
            if self._mice:  # e.g. charge cable pulled, dongle still connected
                self._mouse_path, pid = list(self._mice.items())[-1]
                logger.info("Switching to %s (%s)", self._mouse_path,
                            self.info.pids.get(pid, f"{pid:#06x}"))
                await self._connect(pid)
        if not self._paths and not self._is_naga_model:
            await self._release_resources()

    async def _release_resources(self) -> None:
        """Last node of an experimental/other mouse gone: the session stays listed
        (disconnected) but holds no input device, grab, held key or learning state."""
        self.router.learn(None)
        self._learning = False
        await self.router.close()
        await self.mapper.release_all()

    def _probe(self, mouse: RazerMouse) -> None:
        """Read-only probe before anything is written."""
        try:
            result = probe(mouse)
        except Exception as e:  # never let a probe bug take the session down
            result = ProbeResult(ok=False, problems=[f"probe crashed: {e}"])
        self.probe_result = result
        if result.ok:
            self._write_ok = True
        elif self.info.verified:
            self._write_ok = True
            logger.warning("Probe of %s failed (%s) - verified model, settings "
                           "are written anyway", self.info.name, "; ".join(result.problems))
        else:
            self._write_ok = False
            logger.warning("Probe of %s failed (%s) - mouse stays write-protected "
                           "until reconnected", self.info.name, "; ".join(result.problems))

    async def _connect(self, pid: int) -> None:
        # two nodes of one model (cable + dongle) can start overlapping connects;
        # only the newest may open anything. Everything after the last await below
        # runs without yielding, so a superseded run never opens a transport.
        self._connect_gen += 1
        gen = self._connect_gen

        def superseded() -> bool:
            return gen != self._connect_gen

        await self._disconnect(reset_mode=False)
        if superseded():
            return
        await self.reload()  # fresh configuration, nothing touches the mouse yet
        if superseded():
            return
        path = None
        for _ in range(10):  # hidraw nodes may appear after the input nodes
            path = self._find_control((pid,))  # the node we were told about
            if path:
                break
            await asyncio.sleep(self._retry_delay)
            if superseded():
                return
        if not path:
            logger.warning("Control channel (hidraw) of %s not found", self.info.name)
            return
        try:
            self._transport = self._transport_factory(path)
        except OSError as e:
            logger.warning("Cannot open %s: %s", path, e)
            return
        mouse = make_mouse(self.info, self._transport, pid)
        self._pid = pid
        self._probe(mouse)
        self._mouse = mouse
        if self._write_ok:
            self._ensure_backup(mouse)  # before anything persistent is written
            if not self.info.verified and not self.backup_path.exists():
                # experimental mouse: nothing to restore from -> never write to it
                self._write_ok = False
                logger.warning("No backup of %s - mouse stays write-protected "
                               "until reconnected", self.info.name)
        self._power_applied = {}
        self._poll_failed = False
        self._mode = None
        naga = self._naga
        if naga is not None:
            self._sync_mode(self.profile)
            vendor_path = self._find_vendor((pid,))
            if vendor_path:
                reader = self._hidraw_reader(vendor_path, self._vendor_report)
                try:
                    reader.start()
                    self._vendor = reader
                except OSError as e:
                    logger.warning("Cannot read %s: %s", vendor_path, e)
            self._absent_zones = {z for z, led in ZONE_LED.items()
                                  if _safe(lambda led=led: naga.brightness(led), None) is None}
        self._hw = {"connected": True, "connection": self.info.pids.get(pid, ""),
                    "battery": _safe(mouse.battery, -1) if self._can("battery") else -1,
                    "charging": bool(_safe(mouse.charging, False)) if self._can("charging")
                    else False,
                    "dpi": -1}
        logger.info("Mouse connected: %s %s", self.info.name, self._hw)
        self._warn_openrazer()
        self._apply_active()
        dpi = _safe(mouse.dpi, None) if self._can("dpi") else None
        self._hw["dpi"] = dpi[0] if dpi else -1
        self.apply_power(self._settings())
        if self._hw["connection"] in WIRELESS and self._can("battery"):
            self._battery = BatteryMonitor(
                mouse, 60, self._on_battery,
                connection=lambda: "wireless" if self._hw["connection"] in WIRELESS
                else self._hw["connection"],
                threshold=lambda: self._settings().low_battery_notify,
                notify=self._low_battery, state=self._battery_warn,
                on_poll=self._after_poll, suspended=lambda: self._paused)
            self._battery.poll()
            self._battery.start()
        self._on_status()

    def _ensure_backup(self, mouse: RazerMouse) -> None:
        """One-time backup of the mouse's own values on the first connect (never
        overwrites an existing one), so the write-back and Restore have a source."""
        if self.backup_path.exists():
            return
        try:
            save_backup(mouse, self.backup_path)
        except (BackupError, OSError, ValueError) as e:
            logger.warning("Backup %s not created: %s", self.backup_path, e)
            return
        logger.info("Original mouse state backed up: %s", self.backup_path)

    def _after_poll(self, ok: bool) -> None:
        """Wireless poll: after sleep the mouse answers again but has dropped the device
        mode and the volatile (NOSTORE) settings. Detected by an answer after failed
        polls, or (Naga) by a device mode other than the one we set; then re-apply."""
        if not ok:
            self._poll_failed = True
            return
        woke, self._poll_failed = self._poll_failed, False
        if self._mouse is None:
            return
        if not woke:
            naga = self._naga
            if naga is None:
                return
            mode = _safe(naga.device_mode, None)
            if mode is None or mode == device_mode_for(self.profile):
                return
        logger.info("Mouse reachable again - re-applying device mode and profile")
        self._mode = None
        self._sync_mode(self.profile)
        self._apply_active()
        dpi = _safe(self._mouse.dpi, None) if self._can("dpi") else None
        if dpi and self._hw["dpi"] != dpi[0]:
            self._hw["dpi"] = dpi[0]
            self._on_status()

    def _on_battery(self, level: int, charging: bool) -> None:
        if (self._hw["battery"], self._hw["charging"]) != (level, charging):
            self._hw.update(battery=level, charging=charging)
            self._on_status()

    def _low_battery(self, level: int) -> None:
        logger.warning("Battery low (%s): %d %%", self.info.name, level)
        if self._is_naga_model:
            title = tr("daemon", "Naga V2 Pro: Low battery")
        else:
            title = tr("daemon", "{name}: Low battery", name=self.info.name)
        try:
            self._notify(title, tr("daemon", "Battery at {level} % – please charge the mouse.",
                                   level=level))
        except Exception:
            logger.exception("Notification not sent")

    async def _disconnect(self, reset_mode: bool) -> None:
        if self._battery is not None:
            await self._battery.stop()
            self._battery = None
        if self._vendor is not None:
            await self._vendor.close()
            self._vendor = None
        naga = self._naga
        if naga is not None and reset_mode:
            try:
                naga.set_device_mode(NORMAL_MODE)
            except _HW_ERRORS as e:
                logger.warning("Normal mode not restored: %s", e)
        if self._transport is not None:
            close = getattr(self._transport, "close", None)
            if close:
                close()
        was_connected = self._hw["connected"]
        self._transport = self._mouse = None
        self._write_ok = True
        self._mode = None
        self._hw = dict(_DISCONNECTED)
        if was_connected:
            self._on_status()
