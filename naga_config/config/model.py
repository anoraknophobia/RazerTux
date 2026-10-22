"""Configuration model: actions, macros, lighting, profiles, settings."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from evdev import ecodes as E

from naga_config.hw.buttons import ButtonId

MacroMode = Literal["once", "repeat_n", "while_held", "toggle"]
Effect = Literal["off", "static", "breath", "spectrum", "wave", "reactive"]
Zone = Literal["logo", "scroll", "thumb"]

POLL_RATES = (125, 500, 1000)
DPI_MIN, DPI_MAX, MAX_STAGES = 100, 30000, 5


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
    direction: Literal["up", "down"]


Action = (DefaultAction | DisabledAction | KeyAction | ComboAction
          | MacroAction | HypershiftAction | DpiStageAction)

_ACTION_TYPES: dict[str, type] = {
    "default": DefaultAction, "disabled": DisabledAction, "key": KeyAction,
    "combo": ComboAction, "macro": MacroAction, "hypershift": HypershiftAction,
    "dpi_stage": DpiStageAction,
}
_ACTION_NAMES = {cls: name for name, cls in _ACTION_TYPES.items()}


def action_to_dict(a: Action) -> dict[str, Any]:
    d: dict[str, Any] = {"type": _ACTION_NAMES[type(a)]}
    d.update(vars(a))
    return d


def action_from_dict(d: dict[str, Any]) -> Action:
    cls = _ACTION_TYPES.get(d.get("type"))  # type: ignore[arg-type]
    if cls is None:
        raise ValueError(f"Unbekannter Aktionstyp: {d.get('type')!r}")
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
                raise ValueError(f"Unbekannter Schritttyp: {s.get('type')!r}")
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
    `poll_rate`, `lighting`, `scroll_profile`) mean "leave the mouse as is".
    """
    id: str
    name: str
    standard: dict[ButtonId, Action] = field(default_factory=dict)
    hypershift: dict[ButtonId, Action] = field(default_factory=dict)
    dpi_stages: list[tuple[int, int]] = field(default_factory=list)
    active_stage: int = 0  # 1-based when dpi_stages is non-empty
    poll_rate: int | None = None
    lighting: dict[Zone, LightingZone] = field(default_factory=dict)
    scroll_profile: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name,
            "standard": {b.value: action_to_dict(a) for b, a in self.standard.items()},
            "hypershift": {b.value: action_to_dict(a) for b, a in self.hypershift.items()},
            "dpi_stages": [list(s) for s in self.dpi_stages],
            "active_stage": self.active_stage,
            "poll_rate": self.poll_rate,
            "lighting": {z: lz.to_dict() for z, lz in self.lighting.items()},
            "scroll_profile": self.scroll_profile,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Profile:
        return cls(
            id=d["id"], name=d["name"],
            standard={ButtonId(b): action_from_dict(a) for b, a in d["standard"].items()},
            hypershift={ButtonId(b): action_from_dict(a) for b, a in d["hypershift"].items()},
            dpi_stages=[(x, y) for x, y in d["dpi_stages"]],
            active_stage=d["active_stage"],
            poll_rate=d["poll_rate"],
            lighting={z: LightingZone.from_dict(lz) for z, lz in d["lighting"].items()},
            scroll_profile=d["scroll_profile"],
        )


@dataclass
class Settings:
    active_profile: str
    low_battery_notify: int = 15

    def to_dict(self) -> dict[str, Any]:
        return {"active_profile": self.active_profile,
                "low_battery_notify": self.low_battery_notify}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Settings:
        return cls(d["active_profile"], d.get("low_battery_notify", 15))


def default_profile() -> Profile:
    standard: dict[ButtonId, Action] = {b: DefaultAction() for b in ButtonId}
    standard[ButtonId.TOP_1] = DpiStageAction("up")
    standard[ButtonId.TOP_2] = DpiStageAction("down")
    return Profile(id="default", name="Standard", standard=standard)


def validate(profile: Profile, macros: dict[str, Macro] | None = None) -> list[str]:
    """Return human-readable (German) errors; empty list means valid."""
    errors: list[str] = []
    left = profile.standard.get(ButtonId.LEFT, DefaultAction())
    if not isinstance(left, DefaultAction) and not any(
        isinstance(a, KeyAction) and a.code == E.BTN_LEFT
        for a in profile.standard.values()
    ):
        errors.append("Linksklick nicht erreichbar")
    if sum(isinstance(a, HypershiftAction) for a in profile.standard.values()) > 1:
        errors.append("Mehr als eine Hypershift-Taste")
    if any(isinstance(a, HypershiftAction) for a in profile.hypershift.values()):
        errors.append("Hypershift-Taste in der Hypershift-Ebene nicht erlaubt")
    if macros is not None:
        for layer in (profile.standard, profile.hypershift):
            for a in layer.values():
                if isinstance(a, MacroAction) and a.macro_id not in macros:
                    msg = f"Makro fehlt: {a.macro_id}"
                    if msg not in errors:
                        errors.append(msg)
    if profile.dpi_stages:
        if len(profile.dpi_stages) > MAX_STAGES:
            errors.append(f"Maximal {MAX_STAGES} DPI-Stufen erlaubt")
        if any(not DPI_MIN <= v <= DPI_MAX for s in profile.dpi_stages for v in s):
            errors.append(f"DPI muss zwischen {DPI_MIN} und {DPI_MAX} liegen")
        if not 1 <= profile.active_stage <= len(profile.dpi_stages):
            errors.append("Aktive DPI-Stufe ungültig")
    if profile.poll_rate is not None and profile.poll_rate not in POLL_RATES:
        errors.append("Abtastrate muss 125, 500 oder 1000 Hz sein")
    for zone, lz in profile.lighting.items():
        if not 0 <= lz.brightness <= 255:
            errors.append(f"Helligkeit ({zone}) muss zwischen 0 und 255 liegen")
        if any(not 0 <= c <= 255 for c in lz.color):
            errors.append(f"Farbwerte ({zone}) müssen zwischen 0 und 255 liegen")
    return errors
