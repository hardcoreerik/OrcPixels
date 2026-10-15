"""Adaptive Live Data screens (milestone 2): one draw function per screen renders natively
at any panel size from the shared layout context (docs/DESIGN.md), so the Sports and
Entertainment sets need no hand-made 64x64 twin. 64x16 and 64x64 are the tested native
sizes; 16x16 / 64x32 / 256x16 / 96x24 are the adaptive regression set.

Loop rules as for every Live screen: pages flip every `per` frames with the boundary offset
by half a page; strips (8:1+) use a padded marquee that wraps exactly at the loop end.
"""
from __future__ import annotations

import math
import unicodedata

from PIL import Image, ImageDraw

from . import layout as L
from .layout import Ctx, Rect
from .sources import livefeeds as LF

# palette + helpers mirrored from livedata_screens (kept import-free so plugins never
# import livedata_screens, which loads the plugins: no import cycle)
OG, OG2, DOG = (40, 210, 90), (140, 245, 160), (12, 80, 34)
PB, LPB, DPB = (30, 140, 255), (120, 200, 255), (0, 38, 90)
AMB, DAMB, CREAM = (255, 168, 20), (120, 64, 0), (255, 236, 190)
RED, GREY, DIM = (255, 50, 40), (100, 106, 120), (45, 50, 58)
STALE = (255, 40, 30)


def dimc(c, k):
    return tuple(int(v * k) for v in c)


def page_of(k: int, pages: int, per: int) -> int:
    return ((k + per // 2) // per) % pages if pages > 1 else 0


class Screen:
    key = ""
    group = ""
    short = ""
    per = 8
    feed = LF.Feed

MARQ_STEP = 4                       # px per frame (one 3x5 glyph per ~1 s frame)


def clean(s) -> str:
    """Display-safe ASCII (3x5 font): accents folded, other scripts dropped."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return " ".join(s.replace("\u2019", "'").split())


def short_num(n) -> str:
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for d, u in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if n >= d:
            return f"{n / d:.1f}{u}" if n < d * 10 else f"{n / d:.0f}{u}"
    return f"{n:.0f}"


def T(img, x, y, s, c, sc=1) -> int:
    return L.text(img, x, y, s, c, sc)


def RT(img, xr, y, s, c, sc=1) -> int:
    x = xr - L.text_w(s, sc) + 1
    L.text(img, x, y, s, c, sc)
    return x


def fit(s, px, sc=1) -> str:
    return L.fit_text(clean(s), px, sc)


def wrap(s: str, px: int, lines: int, sc=1) -> list[str]:
    words, out, cur = clean(s).upper().split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if L.text_w(t, sc) <= px:
            cur = t
        else:
            if cur:
                out.append(cur)
            cur = fit(w, px, sc)
            if len(out) == lines:
                break
    if cur and len(out) < lines:
        out.append(cur)
    out = out[:lines]
    if out and len(" ".join(out)) < len(clean(s)) and L.text_w(out[-1] + "..", sc) <= px:
        pass
    return out


def scale_for(ctx: Ctx) -> int:
    return 2 if ctx.tier == "xl" or (ctx.tier == "large" and ctx.w >= 96) else 1


def header(img, ctx: Ctx, title: str, col, right: str = "", rcol=GREY) -> int:
    """Title band on panels >= 24 px tall; returns the first body row."""
    sc = scale_for(ctx)
    bh = 5 * sc + 4
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, ctx.w - 1, bh - 1), fill=dimc(col, 0.16))
    d.line((0, bh - 1, ctx.w - 1, bh - 1), fill=dimc(col, 0.45))
    room = ctx.w - 4
    if right and L.text_w(title, sc) + 4 + L.text_w(right, sc) > room:
        right = ""
    T(img, 2, 2, fit(title, room, sc), col, sc)
    if right:
        RT(img, ctx.w - 3, 2, right, rcol, sc)
    return bh + 1


def nodata(img, ctx: Ctx, title: str, col, msg: str, mcol=GREY, detail: str = ""):
    if ctx.h >= 24:
        y = header(img, ctx, title, col)
        for i, ln in enumerate(wrap(msg + (" " + detail if detail else ""), ctx.w - 4, max(1, (ctx.h - y) // 7))):
            T(img, 2, y + 1 + 7 * i, ln, mcol if i == 0 else DIM)
        return
    T(img, 1, 1, fit(title, ctx.w - 2), col)
    if ctx.h >= 14:
        T(img, 1, 9, fit(msg, ctx.w - 2), mcol)


def marquee(img, rect: Rect, parts, k: int, n: int, sc: int = 1):
    from .scenes import draw_marquee, marquee_strip
    strip, step = marquee_strip([(clean(s).upper(), c) for s, c in parts if s], sc, 3 * sc, n, rect.h)
    draw_marquee(img, rect, strip, step, k)


def marquee_len(parts, sc: int = 1) -> int:
    from .scenes import strip_width
    w = strip_width([(clean(s).upper(), c) for s, c in parts if s], sc, 3 * sc)
    return max(16, math.ceil(w / (MARQ_STEP * sc)))


def is_strip(ctx: Ctx) -> bool:
    return ctx.h < 24 and ctx.aspect >= 6


class AdaptiveScreen(Screen):
    """Screen drawn by draw_ctx(ctx, img, v, k, now) at any size."""
    adaptive = True
    category = "other"
    color = CREAM
    per = 8

    def items(self, v) -> list:
        return []

    def per_page(self, ctx: Ctx) -> int:
        return 1

    def pages_at(self, v, ctx: Ctx) -> int:
        n = len(self.items(v)) if v else 0
        return max(1, math.ceil(n / max(1, self.per_page(ctx))))

    def strip_parts(self, v) -> list:
        return []

    def loop_at(self, v, size) -> int:
        ctx = Ctx(*size)
        if v and is_strip(ctx) and self.strip_parts(v):
            return marquee_len(self.strip_parts(v))
        return self.per * (self.pages_at(v, ctx) if v else 1)

    def loop(self, v):                       # 64x16 (legacy callers)
        return self.loop_at(v, (64, 16))

    def draw(self, img, v, k, now):          # 64x16 path of livedata_screens.render
        self.draw_ctx(Ctx(img.width, img.height, now=now), img, v, k, now)

    def draw_ctx(self, ctx, img, v, k, now):
        raise NotImplementedError


def render(scr: AdaptiveScreen, feed, k: int, now: float, size) -> Image.Image:
    w, h = size
    ctx = Ctx(w, h, now=now)
    img = Image.new("RGB", (w, h))
    v = feed.value
    title = str(scr.short)
    if v is None:
        if (feed.error or "").startswith("NotConfigured"):
            nodata(img, ctx, title, scr.color, "SET UP", AMB, "IN LIVE SOURCES")
        elif getattr(feed, "needs_key", False):
            nodata(img, ctx, title, scr.color, "SET KEY", AMB, "SETTINGS > FREE SOURCES")
        elif feed.attempt_at is None:
            nodata(img, ctx, title, scr.color, "LOADING", GREY)
        else:
            nodata(img, ctx, title, scr.color, "NO DATA", AMB, (feed.error or "").split(":")[0])
        return img
    try:
        n = scr.loop_at(v, size)
        scr.draw_ctx(ctx, img, v, k % max(1, n), now)
    except Exception as e:  # noqa: BLE001  (a surprising payload must not kill the stream)
        img = Image.new("RGB", (w, h))
        nodata(img, ctx, title, scr.color, "ERR " + type(e).__name__, RED)
    if feed.stale:
        img.putpixel((w - 1, 0), STALE)
    return img


# ------------------------------------------------------------------ a ranked / list screen
class ListScreen(AdaptiveScreen):
    """Items (main, sub, right) paged at any size; strips get a marquee."""
    tag = ""                                  # short tag on 64x16 line 1

    def row_h(self, ctx):
        return 13 if ctx.h >= 40 else 7

    def per_page(self, ctx):
        if ctx.h < 24:
            return 1
        sc = scale_for(ctx)
        body = ctx.h - (5 * sc + 5)
        cols = max(1, ctx.w // (96 if sc == 2 else 64)) if ctx.aspect >= 1.5 else 1
        return max(1, body // (self.row_h(ctx) * sc)) * cols

    def head_right(self, v) -> str:
        return ""

    def strip_parts(self, v):
        out = [(self.tag or str(self.short), self.color)]
        for i, (m, s, r) in enumerate(self.items(v)[:12]):
            out += [(f"{i + 1}", AMB), (m, CREAM), (s, GREY), ("*", DIM)]
        return out

    def draw_ctx(self, ctx, img, v, k, now):
        items = self.items(v)
        if not items:
            nodata(img, ctx, str(self.short), self.color, self.empty_msg(v), GREY)
            return
        if is_strip(ctx):
            marquee(img, Rect(0, (ctx.h - 7) // 2, ctx.w, 7), self.strip_parts(v), k, self.loop_at(v, (ctx.w, ctx.h)))
            return
        pp = self.per_page(ctx)
        pages = self.pages_at(v, ctx)
        p = page_of(k, pages, self.per)
        page = items[p * pp:(p + 1) * pp]
        blink = (k // 2) % 2 == 0
        if ctx.h < 24:                                       # 64x16 / 16x16 / 96x16
            m, s, r = page[0]
            rank = f"{p + 1}"
            tiny = ctx.w < 40
            if tiny:
                T(img, 0, 0, fit(m, ctx.w), CREAM)
                if ctx.h >= 11:
                    T(img, 0, 6, fit(s or r, ctx.w), GREY)
                if ctx.h >= 16:
                    T(img, 0, 11, rank, AMB)
                return
            x = T(img, 1, 1, rank, AMB) + 2
            tw = ctx.w - x - 1
            lines = wrap(m, tw, 2 if not s and not r else 1)
            T(img, x, 1, lines[0] if lines else "", CREAM)
            y2 = 9 if ctx.h >= 14 else None
            if y2 is not None:
                if len(lines) > 1:
                    T(img, x, y2, lines[1], CREAM)
                else:
                    rr = fit(r, ctx.w // 2) if r else ""
                    rx = ctx.w - 1 - L.text_w(rr) if rr else ctx.w
                    T(img, 1, y2, fit(s, rx - 3), GREY)
                    if rr:
                        RT(img, ctx.w - 2, y2, rr, self.color)
                put_dot(img, 0, 0 if ctx.h < 14 else 0, self.color if blink else dimc(self.color, 0.4))
            return
        sc = scale_for(ctx)
        y0 = header(img, ctx, str(self.short), self.color, self.head_right(v) or f"{p + 1}/{pages}")
        cols = max(1, ctx.w // (96 if sc == 2 else 64)) if ctx.aspect >= 1.5 else 1
        cw = ctx.w // cols
        rows = max(1, pp // cols)
        rh = self.row_h(ctx) * sc
        for i, (m, s, r) in enumerate(page):
            c, rr_ = divmod(i, rows)
            x0, y = c * cw + 1, y0 + rr_ * rh
            rank = str(p * pp + i + 1)
            x = T(img, x0, y, rank, AMB, sc) + sc
            rtxt = fit(r, cw // 2, sc) if r else ""
            T(img, x, y, fit(m, cw - (x - x0) - 2, sc), CREAM, sc)
            if rh >= 12 * sc:
                rx = x0 + cw - 2 - (L.text_w(rtxt, sc) if rtxt else 0)
                T(img, x, y + 6 * sc, fit(s, rx - x - 2, sc), GREY, sc)
                if rtxt:
                    T(img, rx, y + 6 * sc, rtxt, self.color, sc)

    def empty_msg(self, v):
        return "NOTHING NOW"


def put_dot(img, x, y, c):
    if 0 <= x < img.width and 0 <= y < img.height:
        img.putpixel((x, y), c)


__all__ = ["AdaptiveScreen", "ListScreen", "render", "clean", "short_num", "header", "nodata", "marquee",
           "marquee_len", "is_strip", "fit", "wrap", "T", "RT", "scale_for", "OG", "LPB"]
