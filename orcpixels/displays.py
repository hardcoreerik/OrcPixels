"""Display registry: every iPixel Color BLE panel OrcPixels drives (config/displays.json).

Signs are connected strictly by their saved address. (Matching "address OR any LED_BLE_*
name" made a reconnect grab whichever panel advertised first when two were in range.)
Discovering a new panel is an explicit Scan / add display action in the UI.

Per-display state: settings overlay config/displays/<id>/settings.json, playlists and the
slot record under config/displays/<id>/; the primary display keeps the classic files in
config/ so a one-sign install looks exactly as before.
"""
from __future__ import annotations

import contextvars
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
from .paths import CONFIG as _CFG  # noqa: E402
PATH = _CFG / "displays.json"
ADDR_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,23}$")
SIZES = {(64, 16), (64, 64), (32, 32), (96, 16), (64, 20), (32, 16), (16, 16)}

# Fresh install: one unconfigured 64x16 display. It never connects (no address) until a sign
# is picked with Scan / add display in the UI (or `--address` / ORCPIXELS_ADDRESS); the first
# sign added takes over this entry. Your own registry lives in config/displays.json
# (gitignored; see config/displays.example.json).
PRIMARY = {"id": "sign", "label": "Sign", "name": "", "address": "", "width": 64, "height": 16,
           "rotate_180": None, "autoconnect": True}

CURRENT: contextvars.ContextVar[dict | None] = contextvars.ContextVar("orcpixels_display", default=None)


# ------------------------------------------------------------------ registry file
def _norm(d: dict) -> dict:
    out = {"label": "", "name": "", "width": 64, "height": 16, "rotate_180": None,
           "autoconnect": False, "driver": "ipixel", **d}
    out["address"] = out.get("address") or ""
    out["address"] = str(out["address"]).upper()
    out["width"], out["height"] = int(out["width"]), int(out["height"])
    return out


def load() -> list[dict]:
    if PATH.exists():
        try:
            ds = [_norm(d) for d in json.loads(PATH.read_text(encoding="utf-8")).get("displays") or []]
            if ds:
                return ds
        except Exception:  # noqa: BLE001
            pass
    return [dict(PRIMARY)]


def save(displays: list[dict]) -> list[dict]:
    ids = [d["id"] for d in displays]
    if len(set(ids)) != len(ids):
        raise ValueError("display ids must be unique")
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps({"displays": displays}, indent=2) + "\n", encoding="utf-8")
    return displays


def get(display_id: str) -> dict:
    for d in load():
        if d["id"] == display_id:
            return d
    raise KeyError(f"unknown display {display_id!r}")


def primary_id() -> str:
    return load()[0]["id"]


HOST_RE = re.compile(r"^[A-Za-z0-9._:\[\]/-]{1,200}$")
SERIAL_RE = re.compile(r"^(COM\d{1,3}|/dev/[A-Za-z0-9_./-]{1,60})$", re.I)
DRIVER_KEYS = ("driver", "profile", "host", "port", "serial", "options", "rotation", "limits", "layout")


def is_ipixel(d: dict) -> bool:
    return str(d.get("driver") or "ipixel") == "ipixel"


def _validate_driver(d: dict, existing: list[dict] | None, new: bool) -> dict:
    from . import profiles as PF
    prof = PF.get(d["profile"]) if d.get("profile") else {}
    d["driver"] = str(d.get("driver") or prof.get("driver") or "")
    from . import drivers as DR
    if d["driver"] not in DR.driver_names() or d["driver"] == "ipixel":
        raise ValueError(f"unknown driver {d['driver']!r}")
    w, h = int(d.get("width") or prof.get("width") or 64), int(d.get("height") or prof.get("height") or 32)
    if not (4 <= w <= 512 and 4 <= h <= 512):
        raise ValueError("size must be 4..512 pixels per side")
    d["width"], d["height"] = w, h
    host = str(d.get("host") or "").strip()
    if host and not HOST_RE.match(host):
        raise ValueError("host: hostname, IP or URL")
    d["host"] = host
    ser = str(d.get("serial") or "").strip()
    if ser and not SERIAL_RE.match(ser):
        raise ValueError("serial: e.g. COM5 or /dev/ttyACM0")
    d["serial"] = ser
    port = d.get("port")
    d["port"] = int(port) if port not in (None, "", 0) else None
    if d["port"] is not None and not 1 <= d["port"] <= 65535:
        raise ValueError("port 1..65535")
    opts = d.get("options") or {}
    if not isinstance(opts, dict):
        raise ValueError("options must be an object")
    d["options"] = {str(k): v for k, v in opts.items() if isinstance(v, (str, int, float, bool))}
    d["address"] = ""
    if not d.get("id"):
        tag = (host or ser or d["driver"]).split("/")[-1].replace(".", "").replace(":", "")[-6:].lower() or "x"
        d["id"] = re.sub(r"[^a-z0-9_-]", "", f"{d['driver']}{w}x{h}_{tag}")[:24]
    if not ID_RE.match(d["id"]):
        raise ValueError("id: lowercase letters, digits, - or _ (max 24)")
    d["label"] = (str(d.get("label") or prof.get("label") or f"{d['driver']} {w}x{h}")).strip()[:40]
    d["name"] = str(d.get("name") or "")[:40]
    r = d.get("rotate_180")
    d["rotate_180"] = None if r is None else bool(r)
    d["autoconnect"] = bool(d.get("autoconnect", False))
    for e in existing or []:
        if new and e["id"] == d["id"]:
            raise ValueError(f"already registered as {e['id']}")
    return {k: v for k, v in d.items() if k in DRIVER_KEYS + ("id", "label", "name", "address", "width", "height",
                                                            "rotate_180", "autoconnect")}


def _with_driver(d: dict) -> dict:
    d = dict(d)
    if d.get("profile") and not d.get("driver"):
        from . import profiles as PF
        d["driver"] = PF.get(d["profile"])["driver"]
    return d


def validate(d: dict, existing: list[dict] | None = None, new: bool = False) -> dict:
    d = _with_driver(d)
    if not is_ipixel(d):
        return _validate_driver(d, existing, new)
    d.pop("driver", None)
    d.pop("profile", None)
    addr = str(d.get("address") or "").upper().strip()
    if not ADDR_RE.match(addr):
        raise ValueError("address must look like AA:BB:CC:DD:EE:FF")
    d["address"] = addr
    w, h = int(d.get("width") or 64), int(d.get("height") or 16)
    if (w, h) not in SIZES:
        raise ValueError(f"unsupported size {w}x{h}")
    d["width"], d["height"] = w, h
    if not d.get("id"):
        d["id"] = f"d{w}x{h}_" + addr.replace(":", "")[-4:].lower()
    if not ID_RE.match(d["id"]):
        raise ValueError("id: lowercase letters, digits, - or _ (max 24)")
    d["label"] = (str(d.get("label") or f"{w}x{h} {addr[-5:]}")).strip()[:40]
    d["name"] = str(d.get("name") or "")[:40]
    r = d.get("rotate_180")
    d["rotate_180"] = None if r is None else bool(r)
    d["autoconnect"] = bool(d.get("autoconnect", False))
    for e in existing or []:
        if new and (e["id"] == d["id"] or (e.get("address") and e["address"] == addr)):
            raise ValueError(f"already registered as {e['id']} ({e['address']})")
    return d


def configured(d: dict) -> bool:
    drv = str(d.get("driver") or "ipixel")
    if drv == "ipixel":
        return bool(ADDR_RE.match(str(d.get("address") or "").upper()))
    if drv == "virtual":
        return True
    if drv == "serial":
        return bool(d.get("serial"))
    if drv == "tidbyt":
        return bool((d.get("options") or {}).get("device_id"))
    if drv == "udp" and not d.get("host"):
        try:
            from . import profiles as PF
            return str(PF.config_for(d)["protocol"].get("transport")) == "e131"   # multicast
        except Exception:  # noqa: BLE001
            return False
    return bool(d.get("host"))


def add(d: dict) -> dict:
    """Register a sign. The first sign added on a fresh install fills the unconfigured
    primary entry (keeping its id) instead of becoming a second display."""
    ds = load()
    d = _with_driver(d)
    if ds and not configured(ds[0]) and is_ipixel(d) and is_ipixel(ds[0]):
        nd = validate({**d, "id": ds[0]["id"]}, ds[1:], new=True)
        nd["autoconnect"] = True
        save([nd] + ds[1:])
        return nd
    d = validate(d, ds, new=True)
    save(ds + [d])
    return d


def update(display_id: str, **changes) -> dict:
    ds = load()
    for i, d in enumerate(ds):
        if d["id"] == display_id:
            nd = validate({**d, **changes})
            nd["id"] = d["id"]
            ds[i] = nd
            save(ds)
            return nd
    raise KeyError(f"unknown display {display_id!r}")


def remove(display_id: str) -> None:
    ds = load()
    if ds and ds[0]["id"] == display_id:
        raise ValueError("the primary display can't be removed")
    save([d for d in ds if d["id"] != display_id])


# ------------------------------------------------------------------ current display
def current() -> dict:
    return CURRENT.get() or load()[0]


def size() -> tuple[int, int]:
    """Size content is rendered at: the panel itself for iPixel signs, else the curated
    layout size that fits the panel best (the driver auto-fits it, see fit.py)."""
    return render_size(current())


def render_size(d: dict) -> tuple[int, int]:
    w, h = int(d["width"]), int(d["height"])
    if is_ipixel(d):
        return w, h
    from .fit import render_size as rs
    return rs(w, h)


def is_primary() -> bool:
    d = CURRENT.get()
    if d is None:
        return True
    try:
        return d["id"] == primary_id()
    except Exception:  # noqa: BLE001
        return True


def scoped(path: Path) -> Path:
    """Per-display variant of a config file: unchanged for the primary display,
    config/displays/<id>/<name> for the others (so tests' tmp paths stay isolated)."""
    if is_primary():
        return path
    return path.parent / "displays" / current()["id"] / path.name


class use:
    """with displays.use(d): ... (sets CURRENT, restores it afterwards)."""

    def __init__(self, d: dict | None):
        self.d, self.tok = d, None

    def __enter__(self):
        self.tok = CURRENT.set(self.d)
        return self.d

    def __exit__(self, *exc):
        CURRENT.reset(self.tok)
