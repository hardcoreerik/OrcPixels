"""Shared building blocks for the free-API Live sources (2026-10-14): layout templates,
the home location, a shared response cache and the free-API-key registry.

Templates (all adaptive, docs/DESIGN.md; 64x16 and 64x64 are the native sizes):
  ValueScreen      one big number + label + sub line, optional gauge / sparkline / extra pages
  TextScreen       a quote, fact or joke: static title + scrolling body on strips, wrapped pages on panels
  BarsScreen       a small labelled bar chart (hourly forecast, spots per band)
  live_adaptive.ListScreen   ranked rows (main, sub, right), reused as is

Loop rules as for every Live screen: pages flip every `per` (8) frames with the boundary
offset half a page; marquees wrap exactly at the loop end; pulses use k % 4.

Location: home_latlon() uses Settings > Location (home_lat / home_lon) and falls back to
the FT8 home grid, so location sources need no extra setup. country() = the tv_country
setting if set, else a coarse guess from the home location, else US.

Keys: KEYS lists every service that can take a free key, with its signup page. A key is
optional where the service has a public demo key (NASA DEMO_KEY); a screen whose service
needs a key and has none shows SET KEY and is listed as needs_key (no network traffic).
"""
from __future__ import annotations

import math
import os
import threading
import time

from PIL import Image, ImageDraw

from . import layout as L
from . import live_adaptive as A
from . import place as PLACE
from .layout import Rect
from .live_adaptive import AMB, CREAM, DIM, GREY, RED, dimc, page_of
from .sources import livefeeds as LF

UA = {"User-Agent": "OrcPixels (+https://github.com/hardcoreerik/OrcPixels; hobby LED sign)", "Accept": "application/json"}

# ------------------------------------------------------------------ shared response cache
_CACHE: dict[str, tuple[float, object]] = {}
_LOCK = threading.Lock()


def cached(feed: LF.Feed, url: str, ttl: float, headers=None, raw: bool = False):
    """GET through a process-wide cache, so several screens on one API share one request."""
    with _LOCK:
        hit = _CACHE.get(url)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    h = dict(UA)
    h.update(headers or {})
    data = feed.get(url, h).decode("utf-8", "replace") if raw else feed.get_json(url, h)
    with _LOCK:
        _CACHE[url] = (time.time(), data)
    return data


# ------------------------------------------------------------------ home location
def home_latlon() -> tuple[float, float]:
    h = PLACE.home()
    if h:
        return h
    g = str(LF._setting("livedata_ft8_home_grid") or "").strip()
    if g:
        from .sources import hamgeo
        try:
            return hamgeo.grid_latlon(g)
        except ValueError:
            pass
    raise PLACE.NotConfigured("set home_lat / home_lon (Settings > Location) or an FT8 home grid")


# coarse boxes (lat0, lat1, lon0, lon1); first hit wins, so small countries come first
_BOXES = (("NZ", -48, -34, 166, 179), ("JP", 30, 46, 129, 146), ("KR", 33, 39, 125, 130), ("GB", 49.8, 59, -8.5, 2),
          ("IE", 51.3, 55.5, -10.7, -5.9), ("NL", 50.7, 53.6, 3.3, 7.2), ("BE", 49.5, 51.5, 2.5, 6.4),
          ("CH", 45.8, 47.8, 5.9, 10.5), ("AT", 46.4, 49, 9.5, 17.2), ("DK", 54.5, 57.8, 8, 12.7),
          ("PT", 36.9, 42.2, -9.6, -6.2), ("ES", 36, 43.8, -9.3, 3.3), ("FR", 42.3, 51.1, -4.8, 8.2),
          ("IT", 36.6, 47.1, 6.6, 18.5), ("DE", 47.3, 55.1, 5.9, 15), ("PL", 49, 54.9, 14.1, 24.2),
          ("SE", 55.3, 69, 11, 24.2), ("NO", 58, 71.2, 4.6, 31), ("FI", 59.8, 70.1, 20.6, 31.6),
          ("MX", 14.5, 32.7, -118, -86.7), ("AU", -44, -10, 112, 154), ("BR", -34, 5.3, -74, -34.8),
          ("ZA", -35, -22, 16.4, 33), ("US", 18.9, 49.0, -125, -66.9), ("US", 51, 71.5, -170, -129.9),
          ("CA", 41.7, 83, -141, -52.6))


def country() -> str:
    cc = str(PLACE.get("tv_country") or "").strip().upper()
    if len(cc) == 2:
        return cc
    h = PLACE.home()
    if h:
        for c, a0, a1, o0, o1 in _BOXES:
            if a0 <= h[0] <= a1 and o0 <= h[1] <= o1:
                return c
    return "US"


def km(a, b) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


# ------------------------------------------------------------------ free API keys
# service -> label, settings key, signup page, demo fallback ("" = a key is required), env var
KEYS: dict[str, dict] = {
    "nasa": {"label": "NASA (api.nasa.gov)", "setting": "key_nasa", "signup": "https://api.nasa.gov/",
             "fallback": "DEMO_KEY", "env": "NASA_API_KEY",
             "note": "Optional: works with NASA's shared DEMO_KEY (30 requests/hour); a free key takes 1 minute."},
}


def key_for(service: str) -> str:
    k = KEYS.get(service) or {}
    return (os.environ.get(k.get("env") or "") or str(LF._setting(k.get("setting") or "") or "").strip()
            or k.get("fallback", ""))


def key_status() -> list[dict]:
    out = []
    for sid, k in KEYS.items():
        own = bool(os.environ.get(k.get("env") or "") or LF._setting(k["setting"]))
        out.append({"service": sid, "label": k["label"], "signup": k["signup"], "set": own,
                    "optional": bool(k.get("fallback")), "note": k.get("note", ""), "used_by": sorted(k.get("used_by", []))})
    return out


def register_key(sid: str, label: str, signup: str, fallback: str = "", note: str = "") -> None:
    """Declarative catalog sources can introduce a keyed service (setting key_<sid>)."""
    if sid not in KEYS:
        KEYS[sid] = {"label": label, "setting": f"key_{sid}", "signup": signup, "fallback": fallback, "note": note}


class KeyedFeed(LF.Feed):
    """Feed whose API takes a free key: needs_key only when there is no key and no demo key."""
    service = ""

    @property
    def key_env(self):  # type: ignore[override]
        return (KEYS.get(self.service) or {}).get("env")

    @property
    def needs_key(self) -> bool:
        return not key_for(self.service)


# ------------------------------------------------------------------ text helpers
def countdown(ts: float | None, now: float) -> str:
    if ts is None:
        return "TBD"
    s = ts - now
    if s <= 0:
        return "NOW" if s > -3600 else "DONE"
    d, s = divmod(int(s), 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return f"{d}D {h:02d}H"
    if h:
        return f"{h}H {m:02d}M"
    return f"{m}M"


def num(x, nd=0) -> str:
    try:
        return f"{float(x):,.{nd}f}"
    except (TypeError, ValueError):
        return "?"


def put(img, x, y, c):
    if 0 <= x < img.width and 0 <= y < img.height:
        img.putpixel((x, y), c)


def gauge(img, x, y, w, frac, col, h=1):
    d = ImageDraw.Draw(img)
    d.rectangle((x, y, x + w - 1, y + h - 1), fill=dimc(col, 0.18))
    n = max(0, min(w, round(w * max(0.0, min(1.0, frac)))))
    if n:
        d.rectangle((x, y, x + n - 1, y + h - 1), fill=col)


def spark(img, r: Rect, vals, col):
    vals = [float(v) for v in vals if v is not None]
    if len(vals) < 2 or r.w < 4 or r.h < 3:
        return
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    pts = []
    for i in range(r.w):
        v = vals[min(len(vals) - 1, round(i * (len(vals) - 1) / max(1, r.w - 1)))]
        pts.append((r.x + i, r.y + r.h - 1 - round((v - lo) / rng * (r.h - 1))))
    d = ImageDraw.Draw(img)
    for (x, y) in pts:
        d.line((x, y, x, r.y + r.h - 1), fill=dimc(col, 0.25))
    d.line(pts, fill=col)


# ------------------------------------------------------------------ ValueScreen
class ValueScreen(A.AdaptiveScreen):
    """value(v) -> dict(big, unit, label, sub, col, gauge, spark, extras=[(label, value, col)], icon)
    where icon is an optional fn(img, x, y, k) drawing a ~12x12 icon. Extras become pages on 64x16
    and rows on panels."""
    per = 8

    def value(self, v, now) -> dict:
        raise NotImplementedError

    def _pages(self, v, ctx, now=None):
        d = self.value(v, now if now is not None else time.time())
        ex = d.get("extras") or []
        if ctx.h < 24:
            return 1 + math.ceil(len(ex) / 2) if ex else 1
        rows = self._panel_rows(ctx, d)
        return 1 + math.ceil(max(0, len(ex) - rows) / max(1, rows + 3)) if ex else 1

    def _panel_rows(self, ctx, d):
        sc = A.scale_for(ctx)
        used = (5 * sc + 5) + 14 * sc + 7 * sc + (4 if d.get("gauge") is not None else 0)
        return max(0, (ctx.h - used - 2) // (7 * sc))

    def pages_at(self, v, ctx):
        return self._pages(v, ctx) if v else 1

    def strip_parts(self, v):
        d = self.value(v, time.time())
        out = [(d.get("label") or self.short, self.color), ((d.get("big") or "") + (d.get("unit") or ""), d.get("col") or CREAM),
               (d.get("sub") or "", GREY)]
        for lab, val, c in d.get("extras") or []:
            out += [("*", DIM), (lab, GREY), (val, c or CREAM)]
        return out

    def draw_ctx(self, ctx, img, v, k, now):
        d = self.value(v, now)
        if d.get("empty"):
            A.nodata(img, ctx, str(self.short), self.color, d["empty"], GREY)
            return
        col = d.get("col") or CREAM
        big, unit, label, sub = str(d.get("big") or ""), str(d.get("unit") or ""), str(d.get("label") or self.short), str(d.get("sub") or "")
        ex = d.get("extras") or []
        if A.is_strip(ctx):
            A.marquee(img, Rect(0, (ctx.h - 7) // 2, ctx.w, 7), self.strip_parts(v), k, self.loop_at(v, (ctx.w, ctx.h)))
            return
        pages = self.pages_at(v, ctx)
        p = page_of(k, pages, self.per)
        icon = d.get("icon")
        if ctx.h < 24:
            if ctx.w < 40:                                           # 16x16
                A.T(img, 0, 0, A.fit(big + unit, ctx.w), col)
                A.T(img, 0, 6, A.fit(label, ctx.w), self.color)
                A.T(img, 0, 11, A.fit(sub, ctx.w), GREY)
                return
            if p > 0:                                                # extra pages: two label/value rows
                for i, (lab, val, c) in enumerate(ex[(p - 1) * 2:(p - 1) * 2 + 2]):
                    y = 1 + 8 * i
                    xr = ctx.w - 2
                    vx = A.RT(img, xr, y, A.fit(val, ctx.w // 2), c or CREAM)
                    A.T(img, 1, y, A.fit(lab, vx - 3), GREY)
                self._dots(img, ctx, pages, p)
                return
            x = 1
            if icon and ctx.w >= 48:
                icon(img, 0, 2, k)
                x = 14
            sc = 2 if L.text_w(big + unit, 2) <= (ctx.w - x) * 0.6 and ctx.h >= 14 else 1
            if sc == 2:
                bx = A.T(img, x, 3, big, col, 2)
                if unit:
                    A.T(img, bx, 3, unit, dimc(col, 0.8))
                room = ctx.w - bx - (L.text_w(unit) if unit else 0) - 3
                rx = ctx.w - 2
                A.RT(img, rx, 2, A.fit(label, room), self.color)
                A.RT(img, rx, 9, A.fit(sub, room), GREY)
            else:
                A.T(img, x, 1, A.fit(label, ctx.w - x - 1), self.color)
                bx = A.T(img, x, 9, A.fit(big + unit, ctx.w - x - 1), col)
                if sub and bx + 4 < ctx.w:
                    A.RT(img, ctx.w - 2, 9, A.fit(sub, ctx.w - bx - 4), GREY)
            if d.get("gauge") is not None:
                gauge(img, x, 15, ctx.w - x - 1, d["gauge"], col)
            self._dots(img, ctx, pages, p)
            return
        # ---- panels (64x32, 64x64, 96x24 ...)
        sc = A.scale_for(ctx)
        y = A.header(img, ctx, label, self.color, f"{p + 1}/{pages}" if pages > 1 else str(d.get("right") or ""))
        if p == 0:
            ix = 2
            if icon and ctx.h >= 40:
                icon(img, 2, y + 2, k)
                ix = 17
            bsc = max(1, min(4, (ctx.w - ix - 2) // max(1, L.text_w(big + unit) + 1)))
            bsc = min(bsc, 3 if ctx.h >= 56 else 2, max(1, (ctx.h - y - 8) // 7))
            bx = A.T(img, ix, y + 1, big, col, bsc)
            if unit:
                A.T(img, bx, y + 1 + 5 * bsc - 5, unit, dimc(col, 0.8), sc)
            y += 5 * bsc + 3
            if ctx.h >= 40 and icon:
                y = max(y, 9 * sc + 15)
            if sub:
                for ln in A.wrap(sub, ctx.w - 4, 2 if ctx.h >= 56 else 1, sc):
                    A.T(img, 2, y, ln, GREY, sc)
                    y += 6 * sc + 1
            if d.get("gauge") is not None:
                gauge(img, 2, y, ctx.w - 4, d["gauge"], col, 2)
                y += 4
            rows = max(0, (ctx.h - y - 1) // (7 * sc))
            show = ex[:rows]
            if d.get("spark") and len(show) < 2 and ctx.h - y >= 10:
                spark(img, Rect(2, y + 1, ctx.w - 4, ctx.h - y - 2), d["spark"], col)
                return
        else:
            rows = max(1, (ctx.h - y - 1) // (7 * sc))
            first = self._panel_rows(ctx, d)
            start = first + (p - 1) * rows
            show = ex[start:start + rows]
        for lab, val, c in show:
            vx = A.RT(img, ctx.w - 3, y, A.fit(val, ctx.w // 2, sc), c or CREAM, sc)
            A.T(img, 2, y, A.fit(lab, vx - 4, sc), GREY, sc)
            y += 7 * sc

    def _dots(self, img, ctx, pages, p):
        if pages > 1:
            for i in range(min(pages, 8)):
                put(img, ctx.w - 1 - 2 * (min(pages, 8) - 1 - i), ctx.h - 1, self.color if i == p else DIM)


# ------------------------------------------------------------------ TextScreen
class TextScreen(A.AdaptiveScreen):
    """text(v) -> (title, body, attribution). Strips and 64x16: static title, body scrolls."""
    per = 8

    def text(self, v) -> tuple[str, str, str]:
        raise NotImplementedError

    def _lines(self, v, ctx):
        _t, body, _a = self.text(v)
        sc = A.scale_for(ctx)
        return A.wrap(body, ctx.w - 4, 40, sc)

    def _rows(self, ctx):
        sc = A.scale_for(ctx)
        return max(1, (ctx.h - (5 * sc + 5) - 7 * sc - 1) // (6 * sc + 1))

    def _strip_body(self, v):
        t, body, a = self.text(v)
        return [(body, CREAM)] + ([("- " + a, GREY)] if a else [])

    def loop_at(self, v, size):
        ctx = L.Ctx(*size)
        if not v:
            return self.per
        if ctx.h < 24:
            if A.is_strip(ctx):
                return A.marquee_len([(self.text(v)[0], self.color)] + self._strip_body(v))
            return A.marquee_len(self._strip_body(v))
        return self.per * max(1, math.ceil(len(self._lines(v, ctx)) / self._rows(ctx)))

    def pages_at(self, v, ctx):
        return max(1, math.ceil(len(self._lines(v, ctx)) / self._rows(ctx))) if v else 1

    def draw_ctx(self, ctx, img, v, k, now):
        title, body, attr = self.text(v)
        if not body:
            A.nodata(img, ctx, str(self.short), self.color, "NOTHING YET", GREY)
            return
        n = self.loop_at(v, (ctx.w, ctx.h))
        if A.is_strip(ctx):
            A.marquee(img, Rect(0, (ctx.h - 7) // 2, ctx.w, 7), [(title, self.color)] + self._strip_body(v), k, n)
            return
        if ctx.h < 24:
            A.T(img, 1, 0 if ctx.h < 14 else 1, A.fit(title, ctx.w - 2), self.color)
            if ctx.h >= 14:
                A.marquee(img, Rect(0, ctx.h - 7, ctx.w, 7), self._strip_body(v), k, n)
            return
        sc = A.scale_for(ctx)
        pages = self.pages_at(v, ctx)
        p = page_of(k, pages, self.per)
        y = A.header(img, ctx, title, self.color, f"{p + 1}/{pages}" if pages > 1 else "")
        rows = self._rows(ctx)
        for ln in self._lines(v, ctx)[p * rows:(p + 1) * rows]:
            A.T(img, 2, y, ln, CREAM, sc)
            y += 6 * sc + 1
        if attr:
            A.RT(img, ctx.w - 3, ctx.h - 6 * sc, A.fit("- " + attr, ctx.w - 4, sc), GREY, sc)


# ------------------------------------------------------------------ BarsScreen
class BarsScreen(A.AdaptiveScreen):
    """bars(v) -> (title, [(label, value, col)], right_text). Values >= 0."""
    per = 8

    def bars(self, v) -> tuple[str, list, str]:
        raise NotImplementedError

    def loop_at(self, v, size):
        ctx = L.Ctx(*size)
        if v and A.is_strip(ctx):
            return A.marquee_len(self.strip_parts(v))
        return self.per

    def strip_parts(self, v):
        t, bars, r = self.bars(v)
        out = [(t, self.color)]
        for lab, val, c in bars:
            out += [(lab, GREY), (f"{val:g}" if isinstance(val, (int, float)) else str(val), c or CREAM)]
        return out

    def fmt(self, val) -> str:
        return f"{val:g}"

    def floor(self, vals) -> float:
        """Bar baseline: 0 for counts; screens with a narrow range (temperatures) override."""
        return 0.0

    def draw_ctx(self, ctx, img, v, k, now):
        title, bars, right = self.bars(v)
        if not bars:
            A.nodata(img, ctx, str(self.short), self.color, "NO DATA YET", GREY)
            return
        if A.is_strip(ctx):
            A.marquee(img, Rect(0, (ctx.h - 7) // 2, ctx.w, 7), self.strip_parts(v), k, self.loop_at(v, (ctx.w, ctx.h)))
            return
        d = ImageDraw.Draw(img)
        vals = [float(b[1]) for b in bars]
        lo = min(self.floor(vals), min(vals))
        hi = max(vals)
        rng = (hi - lo) or 1.0
        n = len(bars)
        if ctx.h < 24:
            rw = L.text_w(right) if right and ctx.w >= 48 else 0
            A.T(img, 1, 0, A.fit(title, ctx.w - 2 - (rw + 3 if rw else 0)), self.color)
            if rw:
                A.RT(img, ctx.w - 2, 0, right, GREY)
            area = Rect(0, 6, ctx.w, ctx.h - 6)
            labels = False
            sc = 1
        else:
            sc = A.scale_for(ctx)
            y0 = A.header(img, ctx, title, self.color, right)
            foot = 6 * sc if ctx.h >= 40 else 0
            area = Rect(0, y0 + 1, ctx.w, ctx.h - y0 - 1 - (6 * sc + 1) - foot)
            labels = True
        bw = max(1, area.w // n)
        x0 = (area.w - bw * n) // 2
        every = max(1, math.ceil((max(L.text_w(b[0], sc) for b in bars) + 1) / bw)) if labels else 1
        for i, (lab, val, c) in enumerate(bars):
            x = x0 + i * bw
            frac = (float(val) - lo) / rng
            h = max(1 if float(val) > lo or (float(val) and lo == 0) else 0, round(frac * (area.h - 1)) + (1 if lo else 0))
            h = min(area.h, h)
            if h:
                d.rectangle((x, area.y + area.h - h, x + max(0, bw - 2), area.y + area.h - 1), fill=c or self.color)
            if labels and i % every == 0:
                A.T(img, x, area.y + area.h + 1, lab, GREY, sc)
        if ctx.h >= 40:
            hi_i = vals.index(hi)
            A.T(img, 2, ctx.h - 6 * sc, A.fit("MAX " + self.fmt(hi) + " AT " + bars[hi_i][0], ctx.w - 4, sc), CREAM, sc)


__all__ = ["ValueScreen", "TextScreen", "BarsScreen", "KeyedFeed", "cached", "home_latlon", "country", "km",
           "KEYS", "key_for", "key_status", "countdown", "num", "gauge", "spark", "UA", "AMB", "RED"]
