"""Push API (2026-10-15): other local programs push a status / progress screen or a short
"done" flash to the sign without knowing anything about BLE or presets.

    POST /api/push/status {source, title, step, pct, detail?, ttl_s}   progress screen
    POST /api/push/flash  {source, title, headline, sub?, seconds}     short done flash
    POST /api/push/clear  {source?}                                    end it now
    GET  /api/push                                                     board + engine state
    GET  /api/health                                                   cheap up-check (web.py)

Rules (docs/PUSH_API.md):
  * It is a live source like OrcSDR Live / Live Data (PushLive subclasses the same engine):
    the first fresh push takes over the sign, stopping the other live source / Auto status.
  * When nothing fresh is left (status older than its ttl_s, flash over, or cleared) the
    sign goes back to what it showed before: the live source / Auto status that was running,
    else the playlist / preset (resume_hint), else the last frame, else the playlist fallback.
  * The user always wins: a preset / text / image / playlist / Live start while a push is on
    the sign stops it and MUTES the sources that were pushing until their session ends
    (clear, flash, or ttl expiry). Later pushes from them are accepted but not shown.
  * Legacy transport only (the fast transports freeze the sign), at most 1 frame per
    interval (>= 1 s), and only when the frame changed: frequent pct updates coalesce to
    the newest value; the bar is quantised to pixels so small steps cost nothing.
  * Fail-soft: the routes answer in milliseconds and never wait for the sign.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Union

from PIL import Image, ImageDraw
from fastapi import Request
from pydantic import BaseModel

from . import layout as L
from .layout import Ctx
from .orcsdr_live import OrcSDRLive

log = logging.getLogger("orcpixels.push")

DEFAULT_TTL_S = 30.0
MIN_TTL_S, MAX_TTL_S = 2.0, 3600.0
DEFAULT_FLASH_S = 5.0
MIN_FLASH_S, MAX_FLASH_S = 2.0, 30.0
MIN_INTERVAL = 1.0                 # legacy transport: ~1 fps at most
PAGE_FRAMES = 3                    # long step text pages every 3 frames (~3 s)
MAX_LEN = {"source": 40, "title": 24, "step": 120, "detail": 120, "headline": 24, "sub": 48}

# palette (matches the live screens)
AMB, CREAM, WHITE = (255, 168, 20), (255, 236, 190), (235, 235, 235)
OG, OG2, DOG = (40, 210, 90), (140, 245, 160), (12, 80, 34)
GREY, DIM, FRAME = (100, 106, 120), (45, 50, 58), (34, 38, 46)


def clean(s, n: int) -> str:
    from .live_adaptive import clean as _clean
    return _clean(s)[:n]


def clamp(v, lo, hi, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if v != v:                     # NaN
        return default
    return max(lo, min(hi, v))


# ------------------------------------------------------------------ board (pure state)
@dataclass
class Status:
    source: str
    title: str
    step: str
    pct: Optional[float]
    detail: str
    ttl_s: float
    updated: float
    started: float


@dataclass
class Flash:
    source: str
    title: str
    headline: str
    sub: str
    until: float
    posted: float
    ok: bool = True
    tag: str = "DONE"


@dataclass
class Board:
    """Latest status / flash per source. Only the newest value is kept (coalescing)."""
    statuses: dict = field(default_factory=dict)
    flashes: dict = field(default_factory=dict)
    muted: set = field(default_factory=set)
    clock: object = time.monotonic

    def prune(self, now: float | None = None):
        now = self.clock() if now is None else now
        for k, s in list(self.statuses.items()):
            if now - s.updated > s.ttl_s:
                del self.statuses[k]
        for k, f in list(self.flashes.items()):
            if now >= f.until:
                del self.flashes[k]
        live = set(self.statuses) | set(self.flashes)
        self.muted &= live                 # a muted session ended -> the source may show again

    def set_status(self, source, title="", step="", pct=None, detail="", ttl_s=DEFAULT_TTL_S) -> Status:
        now = self.clock()
        self.prune(now)
        source = clean(source, MAX_LEN["source"]) or "default"
        old = self.statuses.get(source)
        self.flashes.pop(source, None)     # a status after a flash = a new run
        s = Status(source, clean(title, MAX_LEN["title"]) or source, clean(step, MAX_LEN["step"]),
                   None if pct is None else clamp(pct, 0.0, 100.0, None),
                   clean(detail or "", MAX_LEN["detail"]),
                   clamp(ttl_s, MIN_TTL_S, MAX_TTL_S, DEFAULT_TTL_S), now,
                   old.started if old else now)
        self.statuses[source] = s
        return s

    def set_flash(self, source, title="", headline="", sub="", seconds=DEFAULT_FLASH_S,
                  ok: bool = True, tag: str = "") -> Flash:
        now = self.clock()
        self.prune(now)
        source = clean(source, MAX_LEN["source"]) or "default"
        self.statuses.pop(source, None)    # the run is done; no clear needed after a flash
        f = Flash(source, clean(title, MAX_LEN["title"]) or source,
                  clean(headline, MAX_LEN["headline"]) or "DONE", clean(sub or "", MAX_LEN["sub"]),
                  now + clamp(seconds, MIN_FLASH_S, MAX_FLASH_S, DEFAULT_FLASH_S), now, bool(ok),
                  clean(tag, 8).upper() or ("DONE" if ok else "FAIL"))
        self.flashes[source] = f
        return f

    def clear(self, source: str | None = None) -> int:
        if not source:
            n = len(set(self.statuses) | set(self.flashes))
            self.statuses.clear(), self.flashes.clear(), self.muted.clear()
            return n
        source = clean(source, MAX_LEN["source"])
        n = int(source in self.statuses or source in self.flashes)
        self.statuses.pop(source, None)
        self.flashes.pop(source, None)
        self.muted.discard(source)
        return n

    def mute_active(self):
        self.prune()
        self.muted |= set(self.statuses) | set(self.flashes)

    def current(self, now: float | None = None):
        """-> ("flash", Flash) | ("status", Status) | None. Flashes win; newest first."""
        self.prune(now)
        fl = [f for f in self.flashes.values() if f.source not in self.muted]
        if fl:
            return "flash", max(fl, key=lambda f: f.posted)
        st = [s for s in self.statuses.values() if s.source not in self.muted]
        if st:
            return "status", max(st, key=lambda s: s.updated)
        return None

    def as_json(self) -> dict:
        self.prune()
        now = self.clock()
        cur = self.current(now)
        return {"current": None if cur is None else {"kind": cur[0], "source": cur[1].source},
                "muted": sorted(self.muted),
                "statuses": [{"source": s.source, "title": s.title, "step": s.step, "pct": s.pct,
                              "detail": s.detail, "ttl_s": s.ttl_s,
                              "expires_in_s": round(s.ttl_s - (now - s.updated), 1)}
                             for s in self.statuses.values()],
                "flashes": [{"source": f.source, "title": f.title, "headline": f.headline, "sub": f.sub,
                             "ok": f.ok, "tag": f.tag,
                             "ends_in_s": round(f.until - now, 1)} for f in self.flashes.values()]}


# ------------------------------------------------------------------ rendering
def _wrap(s: str, px: int, sc: int = 1) -> list[str]:
    """Word wrap to lines of px (3x5 font, uppercase); long words are hard-split."""
    out, cur = [], ""
    for w in s.upper().split():
        while L.text_w(w, sc) > px:                    # hard split an over-long word
            n = len(w)
            while n > 1 and L.text_w(w[:n], sc) > px:
                n -= 1
            if cur:
                out.append(cur)
                cur = ""
            out.append(w[:n])
            w = w[n:]
        t = (cur + " " + w).strip()
        if L.text_w(t, sc) <= px:
            cur = t
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out or [""]


def _pages(lines: list[str], per: int) -> list[list[str]]:
    return [lines[i:i + per] for i in range(0, len(lines), per)] or [[""]]


def pct_text(p: Optional[float]) -> str:
    return "" if p is None else f"{int(p + 0.5) if p < 99.5 or p >= 100 else 99}%"


def elapsed_text(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def _bar(img, x0, y0, x1, y1, pct: Optional[float], k: int):
    """Outlined bar; pct None = indeterminate marching segment (seamless: wraps exactly)."""
    d = ImageDraw.Draw(img)
    d.rectangle((x0, y0, x1, y1), outline=FRAME, fill=(0, 0, 0))
    ix0, iy0, ix1, iy1 = x0 + 1, y0 + 1, x1 - 1, y1 - 1
    if ix1 < ix0 or iy1 < iy0:
        return
    iw = ix1 - ix0 + 1
    if pct is None:
        seg = max(3, iw // 5)
        step = max(1, iw // 16)
        period = iw + seg
        n = -(-period // step)
        x = ix0 - seg + (k % n) * step
        for xx in range(max(ix0, x), min(ix1, x + seg - 1) + 1):
            c = OG2 if xx == min(ix1, x + seg - 1) else OG
            d.line((xx, iy0, xx, iy1), fill=c)
        return
    fill = int(round(iw * pct / 100.0))
    if pct > 0 and fill == 0:
        fill = 1
    for xx in range(ix0, ix0 + fill):
        d.line((xx, iy0, xx, iy1), fill=OG)
    if 0 < fill < iw:
        d.line((ix0 + fill - 1, iy0, ix0 + fill - 1, iy1), fill=OG2)   # bright leading edge
    elif fill >= iw:
        d.line((ix0, iy0, ix1, iy0), fill=OG2)


def render_status(s: Status, size, k: int = 0, now: float | None = None) -> Image.Image:
    w, h = size
    ctx = Ctx(w, h)
    img = Image.new("RGB", (w, h))
    now = time.monotonic() if now is None else now
    right = pct_text(s.pct) if s.pct is not None else elapsed_text(now - s.started)
    if h < 40:                                   # strips / small / medium: title+pct, step, bar
        bar_h = 4 if h >= 16 else 3 if h >= 12 else 2
        by0 = h - bar_h
        rw = L.text_w(right)
        top = 0
        if h >= 11:
            if w >= 40:
                room = w - 2 - (rw + 3 if right else 0)
                L.text(img, 1, 0, L.fit_text(s.title, room), AMB)
                if right:
                    L.text(img, w - rw - 1, 0, right, CREAM)
            elif right:                           # 16 px wide: no room for a title
                L.text(img, max(0, (w - rw) // 2), 0, right, CREAM)
            top = 6
        elif right:                               # tiny: pct only
            L.text(img, max(0, (w - rw) // 2), 0, right, CREAM)
        rows = max(0, (by0 - top) // 6) if w >= 40 else 0
        if rows:
            sl = _wrap(s.step, w - 2)
            lines = [(ln, WHITE) for ln in sl] + ([(ln, GREY) for ln in _wrap(s.detail, w - 2)] if s.detail else [])
            pages = _pages(lines, rows)
            for i, (ln, c) in enumerate(pages[(k // PAGE_FRAMES) % len(pages)]):
                L.text(img, 1, top + i * 6, ln, c)
        _bar(img, 0, by0, w - 1, h - 1, s.pct, k)
        return img
    # tall panels (64x64 native): header, wrapped step, big pct, bar, detail
    from .live_adaptive import header, scale_for
    sc = scale_for(ctx)
    y = header(img, ctx, s.title, AMB, "LIVE" if w >= 48 else "", GREY)
    lh = 6 * sc
    big = max(1, min(4, (h - y) // 18, (w - 4) // max(1, L.text_w(right or "0") + 1))) if w >= 48 else 1
    bar_h = max(4, h // 10)
    detail_rows = 1 if (s.detail and h >= 48) else 0
    bar_y1 = h - 2 - detail_rows * (lh + 1)
    bar_y0 = bar_y1 - bar_h + 1
    big_h = 5 * big
    big_y = bar_y0 - 3 - big_h
    step_rows = max(1, (big_y - 2 - y) // lh)
    lines = _wrap(s.step, w - 4, sc)
    pages = _pages(lines, step_rows)
    pg = pages[(k // PAGE_FRAMES) % len(pages)]
    for i, ln in enumerate(pg):
        L.text(img, 2, y + 1 + i * lh, ln, WHITE, sc)
    if right:
        bw = L.text_w(right, big)
        L.text(img, (w - bw) // 2, big_y, right, CREAM if s.pct is not None else GREY, big)
    _bar(img, 1, bar_y0, w - 2, bar_y1, s.pct, k)
    if detail_rows:
        dl = _wrap(s.detail, w - 4, sc)
        L.text(img, 2, bar_y1 + 2, dl[(k // PAGE_FRAMES) % len(dl)], GREY, sc)
    return img


_CHECK = ["0000001", "0000011", "0000110", "1001100", "1111000", "0110000"]


def _check(img, x, y, sc, col):
    d = ImageDraw.Draw(img)
    for r, row in enumerate(_CHECK):
        for c, b in enumerate(row):
            if b == "1":
                d.rectangle((x + c * sc, y + r * sc, x + (c + 1) * sc - 1, y + (r + 1) * sc - 1), fill=col)


_CROSS = ["0100010", "0010100", "0001000", "0010100", "0100010", "0000000"]


def _cross(img, x, y, sc, col):
    d = ImageDraw.Draw(img)
    for r, row in enumerate(_CROSS):
        for c, b in enumerate(row):
            if b == "1":
                d.rectangle((x + c * sc, y + r * sc, x + (c + 1) * sc - 1, y + (r + 1) * sc - 1), fill=col)


def _sparkles(img, k: int, cols=(OG2, DOG)):
    """4-frame seamless twinkle in the corners."""
    w, h = img.size
    pts = [(0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)]
    for i, (x, y) in enumerate(pts):
        ph = (k + i) % 4
        c = cols[0] if ph == 0 else cols[1] if ph == 1 else None
        if c:
            img.putpixel((x, y), c)


RED, LRED, DRED = (255, 50, 40), (255, 130, 110), (90, 10, 8)


def render_flash(f: Flash, size, k: int = 0) -> Image.Image:
    """Done flash: green for ok, red for a failure (tag FAIL)."""
    w, h = size
    img = Image.new("RGB", (w, h))
    headline = f.headline.upper()
    hi, mid, lo = (OG2, OG, DOG) if f.ok else (LRED, RED, DRED)
    if h < 40:
        sub = f.sub or f.title
        if f.tag and f.tag != headline and (not f.ok or not f.sub):
            sub = f"{f.tag} {f.sub}".strip()        # FAIL (or DONE with no label) stays visible
        sub = clean(sub, MAX_LEN["sub"]) if h >= 16 else ""
        sub_h = 6 if sub else 0
        sc = 1
        for cand in (4, 3, 2):
            if 5 * cand + sub_h <= h and L.text_w(headline, cand) <= w - 4:
                sc = cand
                break
        block = 5 * sc + sub_h
        hy = max(0, (h - block) // 2) if sc > 1 else max(0, (h - block) // 2)
        L.text(img, (w - L.text_w(L.fit_text(headline, w - 2, sc), sc)) // 2, hy,
               L.fit_text(headline, w - 2, sc), hi, sc)
        if sub:
            subtxt = L.fit_text(sub, w - 2)
            L.text(img, (w - L.text_w(subtxt)) // 2, hy + 5 * sc + 1, subtxt,
                   (CREAM if f.ok else LRED) if f.sub else AMB)
        _sparkles(img, k, (hi, lo))
        return img
    from .live_adaptive import header, scale_for
    ctx = Ctx(w, h)
    sc1 = scale_for(ctx)
    y = header(img, ctx, f.title, mid, f.tag if w >= 48 else "", hi)
    csc = max(1, min(4, (h - y) // 14))
    cw = 7 * csc
    (_check if f.ok else _cross)(img, (w - cw) // 2, y + 3, csc, mid)
    hy = y + 3 + 6 * csc + 4
    big = max(1, min(4, (w - 4) // max(1, L.text_w(headline) + 1)))
    big = min(big, max(1, (h - hy - 9) // 6))
    L.text(img, (w - L.text_w(headline, big)) // 2, hy, L.fit_text(headline, w - 2, big), hi, big)
    if f.sub:
        lines = _wrap(f.sub, w - 4, sc1)[:max(1, (h - (hy + 5 * big + 3)) // (6 * sc1))]
        for i, ln in enumerate(lines):
            L.text(img, (w - L.text_w(ln, sc1)) // 2, hy + 5 * big + 3 + i * 6 * sc1, ln,
                   CREAM if f.ok else LRED, sc1)
    _sparkles(img, k, (hi, lo))
    return img


def render(cur, size, k: int = 0, now: float | None = None) -> Image.Image:
    if cur is None:
        return Image.new("RGB", tuple(size))
    kind, e = cur
    return render_flash(e, size, k) if kind == "flash" else render_status(e, size, k, now)


# ------------------------------------------------------------------ engine
class NoPush(RuntimeError):
    pass


class _Probe:
    base_url = "push"

    def __init__(self, live: "PushLive"):
        self.live = live

    async def status(self):
        cur = self.live.board.current()
        if cur is None:
            raise NoPush("nothing fresh to show")
        return {"kind": cur[0], "source": cur[1].source}

    async def spectrum_raw(self):
        return []


class PushLive(OrcSDRLive):
    NAME = "Push status"
    TRANSPORT_KEY = "push_transport"

    def __init__(self, sign, auto=None, board: Board | None = None):
        super().__init__(sign, auto, client=_Probe.__new__(_Probe), persist=False)
        self.client = _Probe(self)
        self.board = board or Board()
        self.transport = "legacy"                 # never the fast transports (they freeze the sign)
        self.interval = MIN_INTERVAL
        self.poll_s = 0.25
        self.loss_fails, self.loss_min_s = 1, 0.0
        self.restore_settle_s, self.restore_repeat_s = 0.5, 0.0
        self._k = 0
        self._prev: dict | None = None
        self.restored: str | None = None
        self._lock = asyncio.Lock()
        self._arming: asyncio.Task | None = None

    # ---- sampling / rendering
    async def sample_once(self, spectrum: bool = True):
        try:
            self.status = await self.client.status()
            self._poll_ok()
        except NoPush as e:
            self.status = None
            self._poll_failed(e)
        return self.status

    async def _sampler(self):
        while True:
            await self.sample_once()
            await asyncio.sleep(self.poll_s)

    def render(self) -> Image.Image:
        img = render(self.board.current(), self.size(), self._k)
        self._k += 1
        return img

    def showing_name(self) -> str:
        cur = self.board.current()
        return f"Push: {cur[1].title}" if cur else self.NAME

    async def _send_frame(self, img: Image.Image):
        """Legacy transport only: one acked RGB PNG per frame."""
        from .activity import labelled
        if not self.sign.connected:           # unsupervised sign (CLI/tests): connect here
            await asyncio.wait_for(self.sign.connect(), self.connect_timeout_s)
        with labelled(self.showing_name()[:60]):
            await self._hard(self.sign.send_image(img), self.send_timeout_s + self.connect_timeout_s,
                             "push send")
        self.bytes_sent.append(None)
        if hasattr(self.sign, "display_seq"):
            self._seq = self.sign.display_seq
        self._mark_showing()

    # ---- what to go back to
    def _snapshot(self) -> dict:
        live = next((p for p in self.peers if p is not self and p.enabled), None)
        sign = self.sign
        sh = dict(getattr(sign, "showing", None) or {})
        return {"live": live, "auto": bool(self.auto is not None and getattr(self.auto, "enabled", False)),
                "hint": dict(getattr(sign, "resume_hint", None) or {}) or None, "showing": sh,
                "gif": getattr(sign, "last_gif", None), "frame": getattr(sign, "last_frame", None)}

    async def _restore_prev(self) -> str:
        s = self._prev or {}
        self._prev = None
        sign = self.sign
        if s.get("live") is not None:
            await s["live"].start()
            return s["live"].NAME
        if s.get("auto") and self.auto is not None:
            auto = self.auto
            auto._last = None
            await auto.start()
            return "Auto status"
        hint = s.get("hint") or {}
        if hint.get("kind") == "playlist":
            from . import playlist as PL
            await asyncio.sleep(self.restore_settle_s)
            r = await PL.play(sign)
            return f"playlist {r['playlist']}"
        if hint.get("kind") == "preset":
            from . import presets as PR
            await PR.play(sign, hint["name"])
            return f"preset {hint['name']}"
        sh = s.get("showing") or {}
        kind = sh.get("kind")
        if kind and kind not in ("unknown", "live", "live-fallback") and s.get("frame") is not None:
            if s.get("gif"):
                await sign.send_gif(s["gif"], s["frame"])
            else:
                await sign.send_image(s["frame"])
            if hasattr(sign, "set_showing"):
                sign.set_showing(kind, sh.get("name"), **{k: v for k, v in sh.items()
                                                          if k not in ("kind", "name", "since")})
            return f"{kind} {sh.get('name') or ''}".strip()
        return await self.restore_fallback()

    # ---- lifecycle
    async def start(self, waterfall=None, interval=None):
        async with self._lock:
            if self.enabled:
                return
            self._prev = self._snapshot()
            self._k = 0
            self.restored = None
            await super().start(interval=self.interval)

    def arm(self):
        """Fire-and-forget start (the HTTP route never waits for peers / the sign)."""
        if self.enabled or (self._arming and not self._arming.done()):
            return
        self._arming = asyncio.create_task(self._arm())

    async def _arm(self):
        try:
            await self.start()
        except Exception as e:  # noqa: BLE001
            self.supervisor_error = f"{type(e).__name__}: {e}"
            log.warning("push: could not start: %s", self.supervisor_error)

    async def stop(self, restore: bool = False, mute: bool = True) -> str | None:
        """Someone else (user, another live source, Auto status) took the sign: mute the
        pushing sources until their session ends, never fight the user."""
        if mute and self.enabled:
            self.board.mute_active()
        return await super().stop(restore=False)

    async def _supervise(self):
        me = asyncio.current_task()
        while True:
            try:
                why = await self._stream()
                if why == "takeover":
                    self.board.mute_active()
                    self.state = "off"
                    self.stopped_at = time.time()
                    log.info("push: %s; muted %s", self.stopped_reason, sorted(self.board.muted))
                    return
                async with self._lock:
                    if self._supervisor is not me:
                        return
                    if self.board.current() is not None:   # a push arrived meanwhile
                        continue
                    self._supervisor = None               # disarmed before the restore
                    self.state = "off"
                    self.stopped_at = time.time()
                    self.stopped_reason = "push ended"
                    try:
                        self.restored = await asyncio.wait_for(self._restore_prev(), self.fallback_timeout_s)
                        self.fallback_error = None
                        log.info("push ended; sign back on %s", self.restored)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:  # noqa: BLE001
                        self.fallback_error = f"{type(e).__name__}: {e}"
                        log.warning("push: restore failed: %s", self.fallback_error)
                if self.board.current() is not None:
                    self.arm()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                self.supervisor_error = f"{type(e).__name__}: {e}"
                log.exception("push supervisor error")
                await asyncio.sleep(1.0)

    def label(self) -> str:
        if self.state != "off" and self.ble_paused:
            return "paused: sign reconnecting"
        if self.state == "streaming":
            cur = self.board.current()
            return f"showing {cur[0]} from {cur[1].source}" if cur else "ending"
        return "off"

    def info(self) -> dict:
        out = super().info()
        out.update(restored=self.restored, board=self.board.as_json())
        return out


# ------------------------------------------------------------------ HTTP
class PushStatusReq(BaseModel):
    source: str = "default"
    title: str = ""
    step: str = ""
    pct: Optional[float] = None
    detail: Optional[str] = None
    ttl_s: Optional[float] = None


class PushFlashReq(BaseModel):
    source: str = "default"
    title: str = ""
    headline: str = ""
    sub: Optional[str] = None
    seconds: Optional[float] = None
    ok: bool = True                  # False: red failure flash
    tag: Optional[str] = None        # header word on tall panels (default DONE / FAIL)


# RSA Sandbox compatibility (F:\AI\RSA-Sandbox\rsa_orcpixels.py): fixed shapes, source "rsa"
RSA_SOURCE, RSA_TTL_S = "rsa", 60.0


class RsaStatusReq(BaseModel):
    text: str = ""                   # step name (<= 24 chars shown)
    pct: Optional[float] = None
    line2: Optional[str] = None      # "i/n"
    ttl_s: Optional[float] = None


class RsaFlashReq(BaseModel):
    text: str = "DONE"               # DONE | FAIL
    number: Optional[Union[str, int, float]] = None     # "7.4k" (a bare number is accepted too)
    label: Optional[str] = None      # "LOC"
    ok: Optional[bool] = None        # default: text != FAIL
    seconds: Optional[float] = None


class PushClearReq(BaseModel):
    source: Optional[str] = None


LOOPBACK = ("127.0.0.1", "::1", "localhost", "testclient")


def allowed(client_host: str | None) -> bool:
    """Loopback always; LAN clients only when a lan_token is set (LanGuard has checked it)."""
    if (client_host or "127.0.0.1") in LOOPBACK or (client_host or "").startswith("127."):
        return True
    from . import settings as S
    return bool(str(S.load().get("lan_token") or ""))


def install(app, sign, auto=None) -> PushLive:
    from fastapi import HTTPException

    live = PushLive(sign, auto)
    app.state.push_live = live

    def check(request: Request):
        host = request.client.host if request.client else None
        if not allowed(host):
            raise HTTPException(403, "push API is localhost-only; set a LAN token to allow LAN clients")

    def reply(source: str) -> dict:
        cur = live.board.current()
        src = clean(source, MAX_LEN["source"]) or "default"
        muted = src in live.board.muted
        connected = bool(getattr(sign, "connected", False))
        mine = cur is not None and cur[1].source == src
        if cur is not None and connected:
            live.arm()
        reason = None
        if muted:
            reason = "muted: the sign was taken over by the user; shows again after clear / ttl"
        elif not connected:
            reason = "sign not connected"
        elif not mine and cur is not None:
            reason = f"another source ({cur[1].source}) is newer"
        d = getattr(app.state, "display", None) or {}
        return {"ok": True, "shown": bool(mine and connected and not muted), "muted": muted,
                "reason": reason, "display": d.get("id"), "sign_connected": connected}

    @app.post("/api/push/status")
    async def push_status(r: PushStatusReq, request: Request):
        check(request)
        live.board.set_status(r.source, r.title, r.step, r.pct, r.detail or "",
                              DEFAULT_TTL_S if r.ttl_s is None else r.ttl_s)
        return reply(r.source)

    @app.post("/api/push/flash")
    async def push_flash(r: PushFlashReq, request: Request):
        check(request)
        live.board.set_flash(r.source, r.title, r.headline, r.sub or "",
                             DEFAULT_FLASH_S if r.seconds is None else r.seconds, r.ok, r.tag or "")
        return reply(r.source)

    # ---- RSA Sandbox aliases (same engine, source "rsa")
    @app.post("/api/rsa/status")
    async def rsa_status(r: RsaStatusReq, request: Request):
        check(request)
        line2 = clean(r.line2 or "", 9)
        live.board.set_status(RSA_SOURCE, f"RSA {line2}".strip(), clean(r.text, 24), r.pct, "",
                              RSA_TTL_S if r.ttl_s is None else r.ttl_s)
        return reply(RSA_SOURCE)

    @app.post("/api/rsa/flash")
    async def rsa_flash(r: RsaFlashReq, request: Request):
        check(request)
        tag = clean(r.text, 8).upper() or "DONE"
        ok = (tag != "FAIL") if r.ok is None else bool(r.ok)
        n = r.number
        if isinstance(n, float) and n.is_integer():
            n = int(n)
        if isinstance(n, (int, float)) and not isinstance(n, bool):
            from .live_adaptive import short_num
            n = short_num(n) if abs(n) >= 10000 else str(n)
        number = clean("" if n is None else n, MAX_LEN["headline"])
        live.board.set_flash(RSA_SOURCE, "RSA", number or tag, clean(r.label or "", MAX_LEN["sub"]),
                             DEFAULT_FLASH_S if r.seconds is None else r.seconds, ok, tag)
        return reply(RSA_SOURCE)

    @app.post("/api/rsa/clear")
    async def rsa_clear(request: Request):
        check(request)
        return {"ok": True, "cleared": live.board.clear(RSA_SOURCE)}

    @app.post("/api/push/clear")
    async def push_clear(r: PushClearReq, request: Request):
        check(request)
        n = live.board.clear(r.source)
        return {"ok": True, "cleared": n}     # the engine notices within ~0.25 s and restores

    @app.get("/api/push")
    async def push_info(request: Request):
        check(request)
        return {"live": live.info()}

    return live
