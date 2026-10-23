"""Device layouts: which mouse drawings a model shows and which key element every
button sits on (JSON files in ``assets/layouts``)."""
from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path

from razertux.devices.db import DeviceInfo
from razertux.hw.buttons import ButtonId

LAYOUTS = Path(__file__).parent / "assets" / "layouts"
STYLES = ("filled", "laser")


@dataclass(frozen=True)
class View:
    name: str
    asset: str
    style: str = "filled"  # "laser": line art, glow added at render time
    scale: float = 1.0     # height relative to the first view


@dataclass(frozen=True)
class ButtonSpec:
    view: str
    element: str
    side: str                                  # label column: "left" | "right"
    anchor: tuple[float, float] | None = None  # leader line end inside the element rect
    primary: bool = False                      # the button a click on a shared element opens
    entry: float | None = None                 # y (fraction of the view) where the line meets the key column
    via: tuple[tuple[float, float], ...] = ()  # waypoints (fractions of the view) between entry and end
    through: tuple[str, ...] = ()              # elements the line may run over (a key it cannot get around)


@dataclass(frozen=True)
class Layout:
    template: str
    views: tuple[View, ...]
    buttons: dict[ButtonId, ButtonSpec]

    def view(self, name: str) -> View:
        return next(v for v in self.views if v.name == name)

    def buttons_on(self, side: str) -> list[ButtonId]:
        return [b for b, s in self.buttons.items() if s.side == side]

    def elements(self, view: str) -> list[str]:
        return list(dict.fromkeys(s.element for s in self.buttons.values() if s.view == view))

    def button_for(self, view: str, element: str) -> ButtonId | None:
        """The button a click on `element` stands for (the primary one if shared)."""
        found = [b for b, s in self.buttons.items() if s.view == view and s.element == element]
        return next((b for b in found if self.buttons[b].primary), found[0] if found else None)


def _read(name: str) -> dict:
    return json.loads((LAYOUTS / f"{name}.json").read_text(encoding="utf-8"))


@functools.cache
def _index() -> dict[str, str]:
    """Model-slug prefix -> template name."""
    return dict(_read("index").get("prefixes", {}))


def parse(data: dict, plate: int | None = None) -> Layout:
    views = []
    for v in data["views"]:
        asset = v.get("variants", {}).get(str(plate), v["asset"]) if plate is not None else v["asset"]
        style = v.get("style", "filled")
        if style not in STYLES:
            raise ValueError(f"unknown view style {style!r}")
        views.append(View(v["name"], asset, style, float(v.get("scale", 1.0))))
    # per-plate key tables (e.g. the Naga's side plates) come first, then the fixed buttons
    tables = data.get("plate_buttons", {})
    chosen = tables.get(str(plate), next(iter(tables.values()), {}) if plate is None else {})
    buttons = {}
    for key, s in {**chosen, **data["buttons"]}.items():
        anchor = tuple(s["anchor"]) if "anchor" in s else None
        buttons[ButtonId(key)] = ButtonSpec(
            s["view"], s["element"], s["side"], anchor, bool(s.get("primary", False)),
            s.get("entry"), tuple((float(x), float(y)) for x, y in s.get("via", ())),
            tuple(s.get("through", ())))
    return Layout(data["template"], tuple(views), buttons)


def by_name(name: str, plate: int | None = None) -> Layout:
    """A layout file by name (e.g. a template such as "template_ergo")."""
    return parse(_read(name), plate)


def layout_for(info: DeviceInfo, plate: int | None = None) -> Layout:
    """Model file, else the template of the longest matching prefix rule, else generic."""
    candidates = [info.model]
    prefixes = _index()
    best = max((p for p in prefixes if info.model.startswith(p)), key=len, default=None)
    if best is not None:
        candidates.append(prefixes[best])
    candidates.append("generic")
    for name in candidates:
        if (LAYOUTS / f"{name}.json").is_file():
            return parse(_read(name), plate)
    raise FileNotFoundError("generic layout missing")
