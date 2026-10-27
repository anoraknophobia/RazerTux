"""Configuration model: actions, macros, lighting, profiles, settings."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from evdev import ecodes as E

from razertux.devices.db import DeviceInfo
from razertux.hw.button_maps import InputSignal
from razertux.hw.buttons import ButtonId
from razertux.i18n import tr

MacroMode = Literal["once", "repeat_n", "while_held", "toggle"]
Effect = Literal["off", "static", "breath", "spectrum", "wave", "reactive"]
Zone = Literal["logo", "scroll", "thumb"]

POLL_RATES = (125, 500, 1000)
DPI_MIN, DPI_MAX, MAX_STAGES = 100, 30000, 5
# upper end of the DPI sliders in the GUI (user setting; the mouse itself goes to DPI_MAX)
DPI_LIMIT_CHOICES = (6400, 10000, 16000, 20000, 30000)
DEFAULT_DPI_LIMIT = 10000


# --- actions -------------------------------------------------------------

@dataclass(frozen=True)
class DefaultAction:
    """Button keeps its factory behaviour."""


@dataclass(frozen=True)
class DisabledAction:
    """Button does nothing."""


@dataclass(frozen=True)
class KeyAction:
    code: int


@dataclass(frozen=True)
class ComboAction:
    codes: list[int]  # modifiers first


@dataclass(frozen=True)
class MacroAction:
    macro_id: str
    mode: MacroMode
    count: int = 1


@dataclass(frozen=True)
class HypershiftAction:
    """Button acts as the Hypershift modifier."""


@dataclass(frozen=True)
class DpiStageAction:
    """up/down: next/previous stage, clamped; cycle: next stage, wraps to the first."""
    direction: Literal["up", "down", "cycle"]


@dataclass(frozen=True)
class ScrollStageAction:
    """Naga scroll wheel stage (1-6): up/down clamped, cycle wraps (Synapse's front top
    button)."""
    direction: Literal["up", "down", "cycle"]


Action = (DefaultAction | DisabledAction | KeyAction | ComboAction
          | MacroAction | HypershiftAction | DpiStageAction | ScrollStageAction)

_ACTION_TYPES: dict[str, type] = {
    "default": DefaultAction, "disabled": DisabledAction, "key": KeyAction,
    "combo": ComboAction, "macro": MacroAction, "hypershift": HypershiftAction,
    "dpi_stage": DpiStageAction, "scroll_stage": ScrollStageAction,
}
_ACTION_NAMES = {cls: name for name, cls in _ACTION_TYPES.items()}


def action_to_dict(a: Action) -> dict[str, Any]:
    d: dict[str, Any] = {"type": _ACTION_NAMES[type(a)]}
    d.update(vars(a))
    return d


def action_from_dict(d: dict[str, Any]) -> Action:
    cls = _ACTION_TYPES.get(d.get("type"))  # type: ignore[arg-type]
    if cls is None:
        raise ValueError(f"Unknown action type: {d.get('type')!r}")
    return cls(**{k: v for k, v in d.items() if k != "type"})


# --- macros --------------------------------------------------------------

@dataclass(frozen=True)
class KeyStep:
    code: int
    down: bool


@dataclass(frozen=True)
class ButtonStep:
    code: int
    down: bool


@dataclass(frozen=True)
class DelayStep:
    ms: int


MacroStep = KeyStep | ButtonStep | DelayStep
_STEP_TYPES: dict[str, type] = {"key": KeyStep, "button": ButtonStep, "delay": DelayStep}
_STEP_NAMES = {cls: name for name, cls in _STEP_TYPES.items()}


@dataclass
class Macro:
    id: str
    name: str
    steps: list[MacroStep] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name,
                "steps": [{"type": _STEP_NAMES[type(s)], **vars(s)} for s in self.steps]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Macro:
        steps = []
        for s in d["steps"]:
            step_cls = _STEP_TYPES.get(s.get("type"))
            if step_cls is None:
                raise ValueError(f"Unknown step type: {s.get('type')!r}")
            steps.append(step_cls(**{k: v for k, v in s.items() if k != "type"}))
        return cls(d["id"], d["name"], steps)


# --- lighting / profile / settings --------------------------------------

@dataclass
class LightingZone:
    effect: Effect
    color: tuple[int, int, int]
    brightness: int

    def to_dict(self) -> dict[str, Any]:
        return {"effect": self.effect, "color": list(self.color),
                "brightness": self.brightness}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LightingZone:
        r, g, b = d["color"]
        return cls(d["effect"], (r, g, b), d["brightness"])


@dataclass
class Profile:
    """A mouse profile.

    `standard` maps buttons to actions. `hypershift` is the second layer
    (active while the Hypershift button is held); a button missing from it
    behaves like DefaultAction. Empty/None hardware fields (`dpi_stages`,
    `poll_rate`, `lighting`) mean "leave the mouse as is".
    `remap_top_buttons` is always True since 0.5.0: RazerTux handles the Naga's top
    buttons itself (as Synapse does), their bindings always apply. A profile saved by an
    older version with False (the firmware handled the top buttons, their bindings were
    ignored) gets "default" on both on load, which is the factory behaviour (front = next
    scroll stage, back = next DPI stage, see TOP_BUTTON_DEFAULTS).
    """
    id: str
    name: str
    standard: dict[ButtonId, Action] = field(default_factory=dict)
    hypershift: dict[ButtonId, Action] = field(default_factory=dict)
    dpi_stages: list[tuple[int, int]] = field(default_factory=list)
    active_stage: int = 0  # 1-based when dpi_stages is non-empty
    poll_rate: int | None = None
    lighting: dict[Zone, LightingZone] = field(default_factory=dict)
    remap_top_buttons: bool = True
    # scroll wheel options of mice that have them: "mode" (0 tactile, 1 free spinning),
    # "acceleration", "smart_reel" (bool); a missing key = leave the mouse as is
    scroll: dict[str, int | bool] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "id": self.id, "name": self.name,
            "standard": {b.value: action_to_dict(a) for b, a in self.standard.items()},
            "hypershift": {b.value: action_to_dict(a) for b, a in self.hypershift.items()},
            "dpi_stages": [list(s) for s in self.dpi_stages],
            "active_stage": self.active_stage,
            "poll_rate": self.poll_rate,
            "lighting": {z: lz.to_dict() for z, lz in self.lighting.items()},
            "remap_top_buttons": self.remap_top_buttons,
        }
        if self.scroll:  # absent = leave the mouse as is (and old files stay unchanged)
            d["scroll"] = dict(self.scroll)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Profile:
        p = cls._from_dict(d)
        if not p.remap_top_buttons:  # saved before 0.5.0 with the firmware owning them
            for b in TOP_BUTTON_DEFAULTS:
                if b in p.standard:
                    p.standard[b] = DefaultAction()
                p.hypershift.pop(b, None)
            p.remap_top_buttons = True
        return p

    @classmethod
    def _from_dict(cls, d: dict[str, Any]) -> Profile:
        return cls(
            id=d["id"], name=d["name"],
            standard={ButtonId(b): action_from_dict(a) for b, a in d["standard"].items()},
            hypershift={ButtonId(b): action_from_dict(a) for b, a in d["hypershift"].items()},
            dpi_stages=[(x, y) for x, y in d["dpi_stages"]],
            active_stage=d["active_stage"],
            poll_rate=d["poll_rate"],
            lighting={z: LightingZone.from_dict(lz) for z, lz in d["lighting"].items()},
            remap_top_buttons=bool(d.get("remap_top_buttons", False)),
            scroll=dict(d.get("scroll") or {}),
        )


THEMES = ("synapse", "system")
IDLE_MIN_S, IDLE_MAX_S = 60, 900
LOW_POWER_MIN, LOW_POWER_MAX = 5, 25
SCROLL_FEATURES = frozenset({"scroll_mode", "scroll_acceleration", "scroll_smart_reel"})
SCROLL_STAGES = 6  # firmware slots 1-6 (05 3a notifications)
# Scroll stages (user-verified: slot 5 = free-spinning Smooth Scroll): slots follow Synapse's list order
_SYNAPSE_STAGE_ORDER = ("Ultra-fine", "Standard", "Distinct", "Adaptive", "Smooth Scroll",
                        "Custom")
# what older versions stored as the default; replaced on load (the user never named these)
_OLD_DEFAULT_STAGE_NAMES = {6: "Ultra-fine"}


def default_stage_names() -> dict[int, str]:
    return dict(enumerate(_SYNAPSE_STAGE_ORDER, start=1))


NAGA_MODEL = "naga_v2_pro"
SCHEMA = 2


# Settings.device_access: "razertux" = RazerTux talks to the hardware itself (hidraw);
# "openrazer" = features the OpenRazer daemon offers go through it
DEVICE_ACCESS = ("razertux", "openrazer")


@dataclass(init=False)
class Settings:
    """Global settings. `active_profiles` maps device model -> active profile id;
    `active_profile` is the Naga V2 Pro's entry (until the GUI is per-model).
    `low_battery_notify` is the desktop warning level in percent
    (0 = off). `scroll_stage_names` are user names for the firmware's scroll-wheel
    stages (slot 1-6). `idle_time` (seconds) and `low_battery_threshold` (percent, the mouse's
    own low power mode) are written to the mouse; None = leave the mouse as is."""
    active_profiles: dict[str, str]
    low_battery_notify: int
    theme: str
    idle_time: int | None
    low_battery_threshold: int | None
    scroll_stage_names: dict[int, str]
    language: str  # "system" | "de" | "en" | "es" | "fr" (GUI restart needed)
    dpi_limit: int  # GUI slider maximum, one of DPI_LIMIT_CHOICES
    last_model: str  # device model the window showed last ("" = none yet)
    osd: bool  # on-screen display on profile / DPI changes
    custom_scroll: dict[str, int]  # Naga "Custom" stage: {"tension": 0-100, "steps": 8-96}
    device_access: str  # one of DEVICE_ACCESS: who talks to the hardware

    def __init__(self, active_profile: str | None = None, low_battery_notify: int = 15,
                 theme: str = "synapse", idle_time: int | None = None,
                 low_battery_threshold: int | None = None,
                 scroll_stage_names: dict[int, str] | None = None,
                 language: str = "system", dpi_limit: int = DEFAULT_DPI_LIMIT,
                 active_profiles: dict[str, str] | None = None, last_model: str = "",
                 osd: bool = True, custom_scroll: dict[str, int] | None = None,
                 device_access: str = "razertux"):
        self.active_profiles = dict(active_profiles or {})
        if active_profile is not None:
            self.active_profiles[NAGA_MODEL] = active_profile
        self.low_battery_notify = low_battery_notify
        self.theme = theme
        self.idle_time = idle_time
        self.low_battery_threshold = low_battery_threshold
        self.scroll_stage_names = default_stage_names() if scroll_stage_names is None \
            else scroll_stage_names
        self.language = language
        self.dpi_limit = dpi_limit
        self.last_model = last_model
        self.osd = osd
        self.custom_scroll = _custom_scroll(custom_scroll)
        self.device_access = device_access if device_access in DEVICE_ACCESS else "razertux"

    @property
    def active_profile(self) -> str:
        return self.active_profiles.get(NAGA_MODEL, "default")

    @active_profile.setter
    def active_profile(self, value: str) -> None:
        self.active_profiles[NAGA_MODEL] = value

    def to_dict(self) -> dict[str, Any]:
        return {"schema": SCHEMA,
                "active_profiles": dict(self.active_profiles),
                # a 0.2.x process still running after the update reads this key
                # strictly (unknown keys are ignored there)
                "active_profile": self.active_profile,
                "low_battery_notify": self.low_battery_notify,
                "theme": self.theme,
                "idle_time": self.idle_time,
                "low_battery_threshold": self.low_battery_threshold,
                "scroll_stage_names": {str(k): v for k, v in
                                       sorted(self.scroll_stage_names.items())},
                "language": self.language,
                "dpi_limit": self.dpi_limit,
                "last_model": self.last_model,
                "osd": self.osd,
                "custom_scroll": dict(self.custom_scroll),
                "device_access": self.device_access}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Settings:
        names = d.get("scroll_stage_names")
        stage_names = default_stage_names() if names is None \
            else {int(k): str(v) for k, v in names.items()}
        if stage_names == _OLD_DEFAULT_STAGE_NAMES:  # migrate the old default
            stage_names = default_stage_names()
        if "active_profiles" in d:
            active = {str(k): str(v) for k, v in d["active_profiles"].items()}
        else:  # schema 1
            active = {NAGA_MODEL: d["active_profile"]}
        return cls(None, d.get("low_battery_notify", 15),
                   d.get("theme", "synapse"), d.get("idle_time"),
                   d.get("low_battery_threshold"), stage_names,
                   str(d.get("language", "system")),
                   _dpi_limit(d.get("dpi_limit")), active_profiles=active,
                   last_model=str(d.get("last_model", "")), osd=bool(d.get("osd", True)),
                   custom_scroll=d.get("custom_scroll"),
                   device_access=str(d.get("device_access", "razertux")))


@dataclass
class DeviceSettings:
    """Per-device settings (devices/<model>.json). `plate` is the Naga V2 Pro's
    side-plate button count; `remap_enabled` switches button remapping on;
    `learned` maps learn buttons to the input signal the user taught."""
    plate: Literal[12, 6, 2] = 12
    remap_enabled: bool = True
    learned: dict[ButtonId, InputSignal] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"plate": self.plate, "remap_enabled": self.remap_enabled,
                "learned": {b.value: {"source": s.source, "type": s.ev_type, "code": s.code}
                            for b, s in self.learned.items()}}

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, remap_default: bool = True) -> DeviceSettings:
        plate = d.get("plate", 12)
        if plate not in (12, 6, 2):
            plate = 12
        return cls(plate, bool(d.get("remap_enabled", remap_default)),
                   {ButtonId(k): InputSignal(str(v["source"]), int(v["type"]), int(v["code"]))
                    for k, v in d.get("learned", {}).items()})


CUSTOM_SCROLL_DEFAULT = {"tension": 50, "steps": 90}  # Synapse's own default for "Custom"


def _custom_scroll(value: Any) -> dict[str, int]:
    """{"tension": 0-100, "steps": 8-96}; anything else falls back to the default."""
    out = dict(CUSTOM_SCROLL_DEFAULT)
    if isinstance(value, dict):
        t, st = value.get("tension"), value.get("steps")
        if type(t) is int and 0 <= t <= 100:
            out["tension"] = t
        if type(st) is int and 8 <= st <= 96:
            out["steps"] = st
    return out


def _dpi_limit(value: Any) -> int:
    return value if value in DPI_LIMIT_CHOICES and isinstance(value, int) else DEFAULT_DPI_LIMIT


# What the Naga's top buttons do out of the box (and under Synapse): front = next scroll
# wheel stage, back = next DPI stage; both wrap around.
TOP_BUTTON_DEFAULTS: dict[ButtonId, Action] = {ButtonId.TOP_1: ScrollStageAction("cycle"),
                                               ButtonId.TOP_2: DpiStageAction("cycle")}


def default_profile() -> Profile:
    standard: dict[ButtonId, Action] = {b: DefaultAction() for b in ButtonId}
    standard.update(TOP_BUTTON_DEFAULTS)
    return Profile(id="default", name="Standard", standard=standard)


def default_profile_for(info: DeviceInfo) -> Profile:
    """Naga V2 Pro as before; other mice keep every button at its factory behaviour."""
    if info.model == NAGA_MODEL:
        return default_profile()
    return Profile(id="default", name="Standard",
                   standard={b: DefaultAction() for b in ButtonId})


def validate(profile: Profile, macros: dict[str, Macro] | None = None) -> list[str]:
    """Return human-readable errors in the current language (razertux.i18n);
    empty list means valid."""
    errors: list[str] = []
    left = profile.standard.get(ButtonId.LEFT, DefaultAction())
    if not isinstance(left, DefaultAction) and not any(
        isinstance(a, KeyAction) and a.code == E.BTN_LEFT
        for a in profile.standard.values()
    ):
        errors.append(tr("validate", "Left click unreachable"))
    if sum(isinstance(a, HypershiftAction) for a in profile.standard.values()) > 1:
        errors.append(tr("validate", "More than one Hypershift key"))
    if any(isinstance(a, HypershiftAction) for a in profile.hypershift.values()):
        errors.append(tr("validate", "Hypershift key not allowed in the Hypershift layer"))
    if macros is not None:
        for layer in (profile.standard, profile.hypershift):
            for a in layer.values():
                if isinstance(a, MacroAction) and a.macro_id not in macros:
                    msg = tr("validate", "Macro missing: {macro}", macro=a.macro_id)
                    if msg not in errors:
                        errors.append(msg)
    if profile.dpi_stages:
        if len(profile.dpi_stages) > MAX_STAGES:
            errors.append(tr("validate", "At most {count} sensitivity stages allowed", count=MAX_STAGES))
        if any(not DPI_MIN <= v <= DPI_MAX for s in profile.dpi_stages for v in s):
            errors.append(tr("validate", "DPI must be between {min} and {max}",
                             min=DPI_MIN, max=DPI_MAX))
        if not 1 <= profile.active_stage <= len(profile.dpi_stages):
            errors.append(tr("validate", "Invalid active sensitivity stage"))
    if profile.poll_rate is not None and profile.poll_rate not in POLL_RATES:
        errors.append(tr("validate", "Polling rate must be 125, 500 or 1000 Hz"))
    mode = profile.scroll.get("mode")
    if "mode" in profile.scroll and (isinstance(mode, bool) or mode not in (0, 1)):
        errors.append(tr("validate", "Invalid scroll wheel mode"))
    for key in ("acceleration", "smart_reel"):
        if key in profile.scroll and not isinstance(profile.scroll[key], bool):
            errors.append(tr("validate", "Invalid scroll wheel option"))
    for zone, lz in profile.lighting.items():
        if not 0 <= lz.brightness <= 255:
            errors.append(tr("validate", "Brightness ({zone}) must be between 0 and 255", zone=zone))
        if any(not 0 <= c <= 255 for c in lz.color):
            errors.append(tr("validate", "Color values ({zone}) must be between 0 and 255", zone=zone))
    return errors
