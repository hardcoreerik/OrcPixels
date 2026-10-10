"""Persisted user settings (config/settings.json).

Multi-display (2026-10-08): the primary display (displays.py) reads and writes
config/settings.json exactly as before. For any other display the PER_DISPLAY keys
(orientation, what Live mode is armed, Live Data selection/timing, 30 s rule, ...) live in
config/displays/<id>/settings.json; everything else (OrcSDR URL, feed locations, ...) is
shared and still read from / written to config/settings.json.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "config" / "settings.json"
DEFAULTS = {
    "rotate_180": False,  # set True if the panel is mounted upside-down
    "orcsdr_url": "",   # OrcSDR companion web console (read-only)
    "orcsdr_live_armed": False,   # web server re-arms OrcSDR Live on start when True
    # 30 s rule: a link setup / idle connection that hasn't put content on the sign within
    # this many seconds is resolved (play the saved playlist, or let go of the sign)
    "sign_stuck_timeout_s": 30,
    "sign_idle_action": "play",   # play (0x8008 saved playlist) | disconnect
}
PER_DISPLAY = {
    "rotate_180", "orcsdr_live_armed", "orcsdr_interval", "orcsdr_waterfall", "orcsdr_transport",
    "livedata_armed", "livedata_screens", "livedata_rotate", "livedata_rotate_s",
    "livedata_interval", "livedata_transport", "ai_buddy", "ai_auto_armed",
    "sign_stuck_timeout_s", "sign_idle_action", "brightness",
}


def _read(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _scope():
    """-> (display dict, overlay path) for a non-primary display, else (None, None)."""
    from . import displays as D
    if D.is_primary():
        return None, None
    d = D.current()
    return d, PATH.parent / "displays" / d["id"] / "settings.json"


def load() -> dict:
    data = dict(DEFAULTS)
    shared = _read(PATH)
    d, overlay = _scope()
    if d is None:
        data.update(shared)
        return data
    data.update({k: v for k, v in shared.items() if k not in PER_DISPLAY})
    data.update({k: v for k, v in _read(overlay).items() if k != "rotate_180"})
    if d.get("rotate_180") is not None:
        data["rotate_180"] = bool(d["rotate_180"])
    return data


def save(data: dict) -> dict:
    d, overlay = _scope()
    PATH.parent.mkdir(parents=True, exist_ok=True)
    if d is None:
        merged = {**_read(PATH), **data}
        PATH.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        return load()
    own = {k: v for k, v in data.items() if k in PER_DISPLAY}
    # a secondary display's rotation lives in its registry entry (config/displays.json), so
    # the Settings toggle and the display editor can never disagree
    if "rotate_180" in own:
        rot = own.pop("rotate_180")
        rot = None if rot is None else bool(rot)
        if d.get("rotate_180") != rot:
            from . import displays as D
            d["rotate_180"] = rot                      # live dict the SignClient reads
            try:
                D.update(d["id"], rotate_180=rot)
            except Exception:  # noqa: BLE001  (unregistered test display: in-memory only)
                pass
    shared = {k: v for k, v in data.items() if k not in PER_DISPLAY}
    if shared:
        PATH.write_text(json.dumps({**_read(PATH), **shared}, indent=2) + "\n", encoding="utf-8")
    if own:
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(json.dumps({**_read(overlay), **own}, indent=2) + "\n", encoding="utf-8")
    return load()


def get(key: str):
    return load().get(key, DEFAULTS.get(key))
