"""Profile library: profiles/*.json (see profiles/README.md)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "profiles"
REQUIRED = ("id", "label", "driver", "width", "height")


def load() -> dict[str, dict]:
    out: dict[str, dict] = {}
    for f in sorted(DIR.glob("*.json")):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except ValueError as e:
            raise ValueError(f"profiles/{f.name}: {e}") from None
        validate(p, f.name)
        if p["id"] in out:
            raise ValueError(f"duplicate profile id {p['id']}")
        out[p["id"]] = p
    return out


def validate(p: dict, where: str = "profile") -> dict:
    from . import drivers as DR
    for k in REQUIRED:
        if k not in p:
            raise ValueError(f"{where}: missing {k}")
    if p["driver"] not in DR.driver_names():
        raise ValueError(f"{where}: unknown driver {p['driver']}")
    w, h = int(p["width"]), int(p["height"])
    if not (1 <= w <= 512 and 1 <= h <= 512):
        raise ValueError(f"{where}: size out of range")
    lim = p.get("limits") or {}
    if float(lim.get("max_fps", 30)) <= 0 or not 0 < int(lim.get("max_brightness", 100)) <= 100:
        raise ValueError(f"{where}: bad limits")
    return p


def get(pid: str) -> dict:
    ps = load()
    if pid not in ps:
        raise KeyError(f"unknown profile {pid!r}")
    return ps[pid]


def config_for(display: dict) -> dict:
    """Driver config: profile <- display entry (host/port/serial/options/limits override)."""
    prof = get(display["profile"]) if display.get("profile") else {}
    cfg = {**prof, **{k: v for k, v in display.items() if v not in (None, "")}}
    cfg["protocol"] = {**(prof.get("protocol") or {})}
    cfg["options"] = {**(display.get("options") or {})}
    cfg["limits"] = {**(prof.get("limits") or {}), **(display.get("limits") or {})}
    lay = {**(prof.get("layout") or {}), **(display.get("layout") or {})}
    lay.setdefault("rotation", int(display.get("rotation") or prof.get("rotation") or 0))
    cfg["layout"] = lay
    cfg["driver"] = display.get("driver") or prof.get("driver") or "ipixel"
    return cfg


def brief() -> list[dict]:
    return [{k: p.get(k) for k in ("id", "label", "driver", "width", "height", "status", "notes", "protocol")}
            for p in load().values()]
