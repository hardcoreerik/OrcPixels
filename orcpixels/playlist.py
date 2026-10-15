"""On-device playlists ("memory"): store presets in the sign's program slots and let the
sign cycle them on its own (no PC / BLE connection needed once uploaded).

Files
  config/playlists.json     named playlists: ordered preset names + optional duration (s)
  config/device_slots.json  local record of what WE wrote to which device slot, the play
                            order last sent to the device, and the measured upload rate.
                            (The device has no command to list its slots; see below.)

Device protocol (details + sources in protocol.py "stored slots / program list"):
  * content -> slot N : normal PNG/GIF transfer with save_slot byte = N (1..100)
  * play order        : 0x8008 set_program_list [N1, N2, ...] (endless loop)
  * show one slot     : 0x8008 with one slot (pypixelcolor show_slot)
  * NO duration field : the device decides how long each item stays; durations stored
                        here are kept for the UI/future use and are NOT sent.
  * NO slot readback  : nothing in pypixelcolor / DonKracho / ipixel-ctrl / ha-ipixel-color
                        queries stored slots; only per-transfer acks (05 00 cmd cmd status,
                        status 3 = stored/complete) confirm a write.
  * Any live send (save slot 0: presets, text, Auto status, OrcSDR Live) halts the
    program list ([DK]); `play` re-sends 0x8008 to resume from the stored slots.

Upload order follows the app / ipixel-ctrl "Send Image Sequence" (set_prg_mode, then the
data): program list first, then each item into consecutive slots, then the program list
again so playback restarts cleanly from item 1 once everything is stored.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from PIL import Image

from . import presets as PR
from . import protocol as P

log = logging.getLogger("orcpixels.playlist")

ROOT = Path(__file__).resolve().parents[1]
from .paths import CONFIG as _CFG  # noqa: E402
PLAYLISTS = _CFG / "playlists.json"
SLOTS = _CFG / "device_slots.json"
DEFAULT_BASE = 1           # the app's own Program List uses slots 1..N as well
DEFAULT_RATE = 1100.0      # bytes/s over BLE (measured ~1.1 KB/s on this sign)
ACK_OVERHEAD_S = 0.6       # per transfer: acks + GATT round trips


def playlists_path() -> Path:
    """PLAYLISTS for the primary display; config/displays/<id>/playlists.json otherwise."""
    from . import displays as D
    return D.scoped(PLAYLISTS)


def slots_path() -> Path:
    from . import displays as D
    return D.scoped(SLOTS)


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _read(path: Path, default: dict) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            log.warning("could not parse %s; using defaults", path)
    return json.loads(json.dumps(default))


def _write(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ playlists
def _seed_default() -> None:
    """A secondary display starts with its own copy of the primary's 'Default' playlist
    (same preset names; they resolve to that display's size, e.g. presets64/), so the
    30 s rule has something to put back. Written once; afterwards it is that display's own."""
    from . import displays as D
    path = playlists_path()
    if D.is_primary() or path.exists():
        return
    src = (_read(PLAYLISTS, {"playlists": {}}).get("playlists") or {}).get("Default")
    data = {"playlists": {}}
    if src:
        items = []
        for it in src.get("items", []):
            try:
                PR.get(it["preset"])
                items.append({"preset": it["preset"], "duration": it.get("duration")})
            except Exception:  # noqa: BLE001
                pass
        if items:
            data["playlists"]["Default"] = {"items": items, "base_slot": int(src.get("base_slot") or DEFAULT_BASE),
                                            "updated": _now(), "seeded_from": "primary Default"}
    _write(path, data)


def load_all() -> dict:
    _seed_default()
    return _read(playlists_path(), {"playlists": {}}).get("playlists") or {}


def get(name: str) -> dict:
    pls = load_all()
    if name not in pls:
        raise KeyError(f"unknown playlist {name!r}; have: {sorted(pls)}")
    return pls[name]


def _norm_items(items: list) -> list[dict]:
    out = []
    for it in items:
        if isinstance(it, str):
            it = {"preset": it}
        name = str(it["preset"])
        PR.get(name)                                   # raises KeyError if unknown
        d = it.get("duration")
        out.append({"preset": name, "duration": (float(d) if d not in (None, "", 0) else None)})
    return out


def save(name: str, items: list, base_slot: int | None = None) -> dict:
    name = name.strip()
    if not name or any(c in name for c in "/\\?#"):
        raise ValueError("playlist name must be non-empty and not contain / \\ ? #")
    if name in ("play", "upload"):
        raise ValueError("'play' and 'upload' are reserved names")
    items = _norm_items(items)
    if not items:
        raise ValueError("playlist needs at least one preset")
    base = int(base_slot or DEFAULT_BASE)
    P._check_slots(range(base, base + len(items)))
    data = _read(playlists_path(), {"playlists": {}})
    data.setdefault("playlists", {})[name] = {"items": items, "base_slot": base, "updated": _now()}
    _write(playlists_path(), data)
    return data["playlists"][name]


def rename(old: str, new: str) -> dict:
    """Local only: rename a playlist definition (and the slot record's active name)."""
    new = new.strip()
    if not new or any(c in new for c in "/\\?#"):
        raise ValueError("playlist name must be non-empty and not contain / \\ ? #")
    if new in ("play", "upload"):
        raise ValueError("'play' and 'upload' are reserved names")
    data = _read(playlists_path(), {"playlists": {}})
    pls = data.setdefault("playlists", {})
    if old not in pls:
        raise KeyError(f"unknown playlist {old!r}")
    if new != old and new in pls:
        raise ValueError(f"a playlist named {new!r} already exists")
    pls[new] = {**pls.pop(old), "updated": _now()}
    _write(playlists_path(), data)
    rec = slot_record()
    if rec.get("active") == old:
        rec["active"] = new
        _save_record(rec)
    return pls[new]


def delete(name: str) -> None:
    """Local only: removes the playlist definition, never touches the device."""
    data = _read(playlists_path(), {"playlists": {}})
    data.get("playlists", {}).pop(name, None)
    _write(playlists_path(), data)


# -------------------------------------------------------------- slot record
def _addr() -> str:
    from . import displays as D
    return str(D.current().get("address") or "").upper()


def slot_record() -> dict:
    """What OrcPixels wrote into this display's program slots. The record carries the sign's
    address: a record written for another panel (e.g. the old shared file) is not trusted,
    so the 30 s rule can never play one sign's slot order on the other."""
    rec = _read(slots_path(), {"slots": {}, "active": None, "rate_bps": DEFAULT_RATE})
    owner = str(rec.get("address") or "").upper()
    if owner and owner != _addr():
        rec = {"slots": {}, "active": None, "rate_bps": rec.get("rate_bps") or DEFAULT_RATE,
               "foreign_record": owner}
    rec.setdefault("slots", {})
    rec.setdefault("active", None)
    rec.setdefault("rate_bps", DEFAULT_RATE)
    return rec


def _save_record(rec: dict):
    rec = {k: v for k, v in rec.items() if k != "foreign_record"}
    rec["address"] = _addr()
    _write(slots_path(), rec)


# ------------------------------------------------------------------ payloads
def _payload(sign, preset: str) -> dict:
    """Device-ready bytes (rotation applied) exactly as a live send would produce."""
    meta = PR.get(preset)
    path = PR.file_path(meta)
    kind = meta.get("kind", "image")
    if kind == "gif":
        dev, upright, first = sign.prepare_gif(path.read_bytes())
        return {"kind": "gif", "bytes": dev, "upright_gif": upright, "frame": first}
    if kind == "text":
        # store text as a rendered image: the device's native-text orientation is global
        # (0x8006) and would also flip the software-rotated images in the same playlist
        from .render import preview_native_text
        img = preview_native_text(meta["text"], meta.get("color", "ffffff"), meta.get("bg_color"))
    else:
        img = Image.open(path)
    dev, upright = sign.prepare_png(img)
    return {"kind": "png", "bytes": dev, "upright_gif": None, "frame": upright}


def estimate_seconds(nbytes: int, rate: float | None = None, transfers: int = 1) -> float:
    rate = rate or DEFAULT_RATE
    return nbytes / rate + ACK_OVERHEAD_S * transfers


# -------------------------------------------------------------------- upload
class Progress:
    """Shared, JSON-able upload progress (polled by the web UI / CLI)."""

    def __init__(self):
        self.reset()

    def reset(self, playlist: str | None = None):
        self.running = False
        self.playlist = playlist
        self.phase = "idle"
        self.item = 0
        self.items = 0
        self.current = None
        self.bytes_sent = 0
        self.bytes_total = 0
        self.started = None
        self.finished = None
        self.estimate_s = None
        self.error = None
        self.log: list[str] = []
        self.results: list[dict] = []

    def note(self, msg: str):
        log.info("playlist: %s", msg)
        self.log.append(f"{_dt.datetime.now().strftime('%H:%M:%S')} {msg}")
        self.log = self.log[-40:]

    def as_dict(self) -> dict:
        el = (time.monotonic() - self.started) if self.started and not self.finished else (
            (self.finished - self.started) if self.started and self.finished else 0.0)
        rate = self.bytes_sent / el if el > 1 and self.bytes_sent else None
        left = (self.bytes_total - self.bytes_sent) / rate if rate else self.estimate_s
        return {"running": self.running, "playlist": self.playlist, "phase": self.phase,
                "item": self.item, "items": self.items, "current": self.current,
                "bytes_sent": self.bytes_sent, "bytes_total": self.bytes_total,
                "pct": round(100 * self.bytes_sent / self.bytes_total, 1) if self.bytes_total else 0,
                "elapsed_s": round(el, 1), "estimate_s": self.estimate_s,
                "eta_s": round(left, 1) if left is not None and self.running else None,
                "rate_bps": round(rate) if rate else None, "error": self.error,
                "log": self.log, "results": self.results}


PROGRESS = Progress()            # the primary display's (CLI + single-sign callers)
_PROGRESS: dict[str, Progress] = {}


def _ctx_progress() -> Progress:
    """Upload progress of the display in context: each sign uploads independently."""
    from . import displays as D
    if D.is_primary():
        return PROGRESS
    return _PROGRESS.setdefault(D.current()["id"], Progress())


async def upload(sign, name: str, progress: Progress | None = None,
                 skip_unchanged: bool = False) -> dict:
    """Write each playlist item into consecutive slots (base_slot..), then set the play
    order. Every write must be acked with status 3 (stored) or the upload aborts."""
    from .activity import LABEL
    tok = LABEL.set(f"playlist upload {name}")
    try:
        return await _upload(sign, name, progress, skip_unchanged)
    finally:
        LABEL.reset(tok)


async def _upload(sign, name: str, progress: Progress | None, skip_unchanged: bool) -> dict:
    pr = progress or _ctx_progress()
    pl = get(name)
    items = pl["items"]
    base = int(pl.get("base_slot") or DEFAULT_BASE)
    slots = list(range(base, base + len(items)))
    P._check_slots(slots)
    rec = slot_record()
    pr.reset(name)
    pr.running, pr.phase, pr.items, pr.started = True, "preparing", len(items), time.monotonic()
    try:
        payloads = []
        for it in items:
            pl_ = _payload(sign, it["preset"])
            pl_["sha1"] = hashlib.sha1(pl_["bytes"]).hexdigest()
            payloads.append(pl_)
        todo = []
        for slot, it, pay in zip(slots, items, payloads):
            old = rec["slots"].get(str(slot))
            same = bool(old and old.get("sha1") == pay["sha1"] and old.get("preset") == it["preset"])
            todo.append(not (skip_unchanged and same))
        pr.bytes_total = sum(len(p["bytes"]) for p, t in zip(payloads, todo) if t)
        pr.estimate_s = round(estimate_seconds(pr.bytes_total, rec.get("rate_bps"), sum(todo) + 2), 1)
        pr.note(f"'{name}': {len(items)} item(s) -> slots {slots[0]}..{slots[-1]}, "
                f"{pr.bytes_total} B to send, est. {pr.estimate_s:.0f} s")

        # images are rotated in software -> device orientation must be 0 (not a slot cmd,
        # so send it BEFORE the program list, per [DK] "while the program list is loading
        # do not send commands which are not taking a slot number")
        await sign._ensure_orientation(0)
        pr.phase = "program_list"
        a = await sign.set_program_list(slots)
        pr.note(f"program list {slots} ack {a['raw']}")

        base_sent = 0
        t_bytes, t_time = 0, 0.0
        for i, (slot, it, pay, do) in enumerate(zip(slots, items, payloads, todo), 1):
            pr.item, pr.current, pr.phase = i, it["preset"], "uploading"
            if not do:
                pr.note(f"slot {slot}: {it['preset']} unchanged, skipped")
                pr.results.append({"slot": slot, "preset": it["preset"], "skipped": True})
                continue
            n = len(pay["bytes"])

            def cb(sent, total, _b=base_sent):
                pr.bytes_sent = _b + sent

            t0 = time.monotonic()
            store = sign.store_gif if pay["kind"] == "gif" else sign.store_png
            from .activity import labelled
            with labelled(f"slot {slot} <- {it['preset']} ({name})"):
                ack = await store(pay["bytes"], slot, progress=cb)
            dt = time.monotonic() - t0
            t_bytes, t_time = t_bytes + n, t_time + dt
            base_sent += n
            pr.bytes_sent = base_sent
            entry = {"preset": it["preset"], "kind": pay["kind"], "bytes": n, "sha1": pay["sha1"],
                     "rotate_180": sign.rotate_180, "playlist": name, "written": _now(),
                     "ack": ack["raw"], "send_s": round(dt, 2)}
            rec["slots"][str(slot)] = entry
            _save_record(rec)                     # record after every confirmed write
            pr.results.append({"slot": slot, **entry})
            pr.note(f"slot {slot}: {it['preset']} {pay['kind']} {n} B in {dt:.1f} s, ack {ack['raw']}")
            sign.last_frame, sign.last_gif = pay["frame"], pay["upright_gif"]

        pr.phase = "play"
        a = await sign.set_program_list(slots)
        pr.note(f"play order {slots} ack {a['raw']}")
        if t_time > 0:
            rec["rate_bps"] = round(t_bytes / t_time)
        rec["active"] = {"playlist": name, "slots": slots,
                         "presets": [it["preset"] for it in items],
                         "durations": [it.get("duration") for it in items],
                         "set_at": _now(), "ack": a["raw"]}
        _save_record(rec)
        if payloads:
            sign.last_frame, sign.last_gif = payloads[0]["frame"], payloads[0]["upright_gif"]
        sign.resume_hint = {"kind": "playlist", "name": name}
        if hasattr(sign, "set_showing"):
            sign.set_showing("playlist", name, slots=slots)
        pr.phase = "done"
        return {"ok": True, "playlist": name, "slots": slots, "results": pr.results,
                "elapsed_s": round(time.monotonic() - pr.started, 1), "rate_bps": rec["rate_bps"]}
    except Exception as e:  # noqa: BLE001
        pr.error, pr.phase = f"{type(e).__name__}: {e}", "error"
        pr.note(f"FAILED: {pr.error}")
        raise
    finally:
        pr.running, pr.finished = False, time.monotonic()


async def play(sign, name: str | None = None) -> dict:
    """(Re)start on-device playback of the slots last uploaded (optionally only if they
    belong to `name`). Needed after any live send, which halts the program list."""
    rec = slot_record()
    act = rec.get("active")
    if not act:
        raise RuntimeError("nothing uploaded yet: run 'playlist upload <name>' first")
    if name and act["playlist"] != name:
        raise RuntimeError(f"device holds playlist {act['playlist']!r}, not {name!r}; upload it first")
    from .activity import labelled
    await sign._ensure_orientation(0)
    with labelled(f"play playlist {act['playlist']}"):
        a = await sign.set_program_list(act["slots"])
    act["played_at"], act["ack"] = _now(), a["raw"]
    _save_record(rec)
    sign.resume_hint = {"kind": "playlist", "name": act["playlist"]}   # re-played after a reconnect
    if hasattr(sign, "set_showing"):
        sign.set_showing("playlist", act["playlist"], slots=act["slots"])
    return {"ok": True, "playlist": act["playlist"], "slots": act["slots"], "ack": a["raw"]}


async def show_slot(sign, n: int) -> dict:
    from .activity import labelled
    with labelled(f"show slot {n}"):
        a = await sign.show_slot(n)
    return {"ok": True, "slot": n, "ack": a["raw"],
            "preset": slot_record()["slots"].get(str(n), {}).get("preset")}


def summary() -> dict:
    rec = slot_record()
    pls = load_all()
    out = {}
    for name, pl in pls.items():
        base = int(pl.get("base_slot") or DEFAULT_BASE)
        total = 0
        for it in pl["items"]:
            try:
                total += PR.file_path(PR.get(it["preset"])).stat().st_size
            except Exception:  # noqa: BLE001
                pass
        out[name] = {**pl, "slots": list(range(base, base + len(pl["items"]))),
                     "approx_bytes": total,
                     "approx_upload_s": round(estimate_seconds(total, rec.get("rate_bps"),
                                                               len(pl["items"]) + 2))}
    return {"playlists": out, "device": rec, "max_slots": P.PROGRAM_SLOT_MAX,
            "upload": _ctx_progress().as_dict()}


# ----------------------------------------------------------------- web routes
# request models at module level: with `from __future__ import annotations` FastAPI can't
# resolve models defined inside install(); POST /api/playlists/{name} used to answer 422
# ("loc": ["query", "r"]) because the body model became an unresolvable query param.
from pydantic import BaseModel  # noqa: E402


class Item(BaseModel):
    preset: str
    duration: Optional[float] = None


class RenameReq(BaseModel):
    name: str


class SaveReq(BaseModel):
    items: list[Item]
    base_slot: Optional[int] = None


def install(app, sign, auto=None):
    from fastapi import HTTPException

    state = {"task": None}

    async def stop_live():
        """Live modes send to slot 0 and would halt the program list: stop them."""
        for attr in ("orcsdr_live", "livedata_live", "push_live"):
            live = getattr(app.state, attr, None)
            if live is not None and live.enabled:
                await live.stop()
                live.stopped_reason = "playlist took over the sign"
        if auto is not None and auto.enabled:
            await auto.stop()

    @app.get("/api/playlists")
    async def pl_list():
        out = summary()
        out["device_info"] = sign.state.device_info     # incl. stored_slots_hint (byte 8)
        return out

    @app.get("/api/playlists/upload")
    async def pl_progress():
        return _ctx_progress().as_dict()

    @app.post("/api/playlists/play")
    async def pl_play(name: Optional[str] = None):
        await stop_live()
        try:
            return await play(sign, name)
        except RuntimeError as e:
            raise HTTPException(409, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"{type(e).__name__}: {e}")

    @app.post("/api/slots/{n}/show")
    async def slot_show(n: int):
        await stop_live()
        try:
            return await show_slot(sign, n)
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"{type(e).__name__}: {e}")

    @app.post("/api/playlists/{name}")
    async def pl_save(name: str, r: SaveReq):
        try:
            return save(name, [i.model_dump() for i in r.items], r.base_slot)
        except (KeyError, ValueError) as e:
            raise HTTPException(400, str(e))

    @app.post("/api/playlists/{name}/rename")
    async def pl_rename(name: str, r: RenameReq):
        try:
            return {"ok": True, "name": r.name.strip(), "playlist": rename(name, r.name)}
        except KeyError as e:
            raise HTTPException(404, str(e))
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.delete("/api/playlists/{name}")
    async def pl_delete(name: str):
        delete(name)
        return {"ok": True}

    @app.post("/api/playlists/{name}/upload")
    async def pl_upload(name: str, skip_unchanged: bool = False):
        t = state["task"]
        if t is not None and not t.done():
            raise HTTPException(409, "an upload is already running")
        try:
            get(name)
        except KeyError as e:
            raise HTTPException(404, str(e))
        await stop_live()

        async def run():
            try:
                await upload(sign, name, prog, skip_unchanged=skip_unchanged)
            except Exception:  # noqa: BLE001  (reported via PROGRESS.error)
                log.exception("playlist upload failed")

        prog = _ctx_progress()
        prog.reset(name)
        prog.running, prog.phase = True, "queued"
        state["task"] = asyncio.create_task(run())
        return prog.as_dict()

    return _ctx_progress()
