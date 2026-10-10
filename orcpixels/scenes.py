"""Adaptive scenes (phase 4 of docs/DESIGN.md): presets that lay themselves out for any
display. Each scene shares one animation (same motion, same loop length) and only
changes composition with the space it gets from the layout context:

    frames(name, w, h) -> (frames, durations_ms)      seamless loop at any size

Layout decisions use real regions (Ctx.rect.split_x / split_y / square) and the detail
tier, not a fixed family table, so in-between shapes (96x24, 96x32, ...) get a proper layout.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageDraw

from . import layout as L
from .layout import Ctx, Rect

OG, OG2, DOG = (40, 210, 90), (140, 245, 160), (12, 80, 34)
PB, LPB, DPB = (30, 140, 255), (120, 200, 255), (0, 38, 90)
AMB, CREAM, RED = (255, 168, 20), (255, 236, 190), (255, 50, 40)
GREY, DIM, WHITE = (100, 106, 120), (45, 50, 58), (225, 238, 255)
TAU = 2 * math.pi


def dimc(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c)


def lerp(a, b, f):
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))


@dataclass
class Scene:
    name: str
    title: str
    family_notes: str
    draw: Callable[[Ctx, Image.Image, int, int], None]      # (ctx, img, k, n)
    loop: Callable[[Ctx], int]
    frame_ms: int = 100


SCENES: dict[str, Scene] = {}


def scene(name, title, notes, loop, frame_ms=100):
    def deco(fn):
        SCENES[name] = Scene(name, title, notes, fn, loop if callable(loop) else (lambda ctx, n=loop: n), frame_ms)
        return fn
    return deco


def frames(name: str, w: int, h: int, now: float | None = None) -> tuple[list[Image.Image], list[int]]:
    sc = SCENES[name]
    ctx = Ctx(w, h) if now is None else Ctx(w, h, now)
    n = sc.loop(ctx)
    out = []
    for k in range(n):
        img = Image.new("RGB", (w, h))
        sc.draw(ctx, img, k, n)
        out.append(img)
    return out, [sc.frame_ms] * n


def info(name: str, w: int, h: int) -> dict:
    ctx = Ctx(w, h)
    return {**ctx.describe(), "name": name, "title": SCENES[name].title, "rating": "adaptive",
            "frames": SCENES[name].loop(ctx)}


# ------------------------------------------------------------------ shared bits
def _draw_parts(img, parts, scale, gap, y) -> int:
    x = 0
    for s, c in parts:
        x = L.text(img, x, y, s, c, scale) + gap - scale
    return x


def strip_width(parts, scale: int, gap: int) -> int:
    return _draw_parts(Image.new("RGB", (4000, 5 * scale)), parts, scale, gap, 0)


def marquee_strip(parts: list[tuple[str, tuple]], scale: int, gap: int, n: int, height: int):
    """Strip image of coloured text parts; width padded so it scrolls an integer number of
    px per frame and wraps exactly after n frames."""
    w = strip_width(parts, scale, gap)
    step = max(1, math.ceil(w / n))
    strip = Image.new("RGB", (step * n, height))
    _draw_parts(strip, parts, scale, gap, (height - 5 * scale) // 2)
    return strip, step


def draw_marquee(img, rect: Rect, strip, step, k):
    off = (k * step) % strip.width
    x = rect.x - off
    while x < rect.x + rect.w:
        img.paste(strip.crop((max(0, rect.x - x), 0, min(strip.width, rect.x + rect.w - x), min(strip.height, rect.h))),
                  (max(rect.x, x), rect.y))
        x += strip.width


def paged(k, n, pages):
    """Page index with boundaries offset half a page (seam mid-page)."""
    per = n // pages
    return ((k + per // 2) // per) % pages


# ================================================================== 1. ADS-B radar
AC = [("ASA312", "FL350", 0.78, 0.6), ("N123AB", "4500", 0.35, 2.2), ("UAL88", "FL280", 0.9, 3.5),
      ("SWA1904", "FL190", 0.6, 4.6), ("HAL21", "FL390", 0.5, 5.6)]


def _targets(k, n):
    out = []
    for i, (cs, alt, r0, th0) in enumerate(AC):
        ph = TAU * k / n
        r = r0 + 0.08 * math.sin(ph + i * 1.3)
        th = th0 + 0.18 * math.sin(ph + i)
        out.append((cs, alt, max(0.12, min(0.95, r)), th))
    return out


def draw_radar(ctx: Ctx, img, R: Rect, k, n, labels: bool):
    d = ImageDraw.Draw(img)
    cx, cy = R.x + (R.w - 1) / 2, R.y + (R.h - 1) / 2
    rad = (min(R.w, R.h) - 1) / 2
    tiny = rad < 7
    d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=DOG if tiny else (20, 120, 50))
    if not tiny:
        d.ellipse([cx - rad / 2, cy - rad / 2, cx + rad / 2, cy + rad / 2], outline=DOG)
        if rad >= 14:
            d.line([cx - rad, cy, cx + rad, cy], fill=(8, 40, 18))
            d.line([cx, cy - rad, cx, cy + rad], fill=(8, 40, 18))
    a = TAU * k / n
    for j, f in ((3, 0.15), (2, 0.3), (1, 0.55), (0, 1.0)):
        aa = a - j * 0.12
        d.line([cx, cy, cx + rad * math.cos(aa), cy + rad * math.sin(aa)], fill=dimc(OG2, f))
    for cs, alt, r, th in _targets(k, n):
        x, y = cx + r * rad * math.cos(th), cy + r * rad * math.sin(th)
        age = ((a - th) % TAU) / TAU
        b = max(0.3, 1 - age * 1.4)
        col = dimc(AMB, b)
        if tiny or rad < 12:
            d.rectangle([round(x) - 1 + (1 if rad < 12 else 0), round(y) - 1 + (1 if rad < 12 else 0), round(x), round(y)],
                        fill=col)
        else:
            d.rectangle([round(x) - 1, round(y) - 1, round(x) + 1, round(y) + 1], fill=col)
        if labels and rad >= 28:
            L.text(img, min(R.x + R.w - L.text_w(cs) - 1, round(x) + 3), max(R.y, round(y) - 6), cs, dimc(CREAM, b))


def radar_info(ctx: Ctx, img, R: Rect, k, n):
    if R.w < 12 or R.h < 5:
        return
    s = 2 if R.h >= 30 and R.w >= 44 else 1
    rows = (R.h + 1) // 6
    head = f"ADS-B {len(AC)}"
    if rows <= 1:          # one line: header then a marquee of callsigns
        hw = L.text_w(head) + 3
        if R.w > hw + 20:
            L.text(img, R.x, R.y + (R.h - 5) // 2, head, AMB)
            R = Rect(R.x + hw, R.y, R.w - hw, R.h)
        strip, step = marquee_strip(sum(([(c, CREAM), (a, GREY)] for c, a, *_ in AC), []), 1, 4, n, R.h)
        draw_marquee(img, R, strip, step, k)
        return
    y = R.y
    L.text(img, R.x, y, L.fit_text(head, R.w, s), AMB, s)
    y += 6 * s + (1 if s > 1 else 0)
    avail = (R.y + R.h - y + 1) // 6
    if avail <= 0:
        return
    if avail == 1 and R.w >= 60:   # long single row: marquee
        strip, step = marquee_strip(sum(([(c, CREAM), (a, GREY)] for c, a, *_ in AC), []), 1, 4, n, 5)
        draw_marquee(img, Rect(R.x, y, R.w, 5), strip, step, k)
        return
    if avail == 1 or R.w < 30:     # one row left: page through targets
        pages = len(AC)
        if n % pages == 0:
            cs, alt, *_ = AC[paged(k, n, pages)]
            L.text(img, R.x, y, L.fit_text(cs, R.w), CREAM)
            if avail >= 2:
                L.text(img, R.x, y + 6, L.fit_text(alt, R.w), GREY)
        return
    per_page = avail
    pages = math.ceil(len(AC) / per_page)
    pg = paged(k, n, pages) if n % pages == 0 else 0
    for i, (cs, alt, *_) in enumerate(AC[pg * per_page:(pg + 1) * per_page]):
        L.text(img, R.x, y + 6 * i, L.fit_text(cs, R.w), CREAM)
        if R.w >= L.text_w(cs) + L.text_w(alt) + 4:
            L.text(img, R.x + R.w - L.text_w(alt), y + 6 * i, alt, GREY)


@scene("adaptive_adsb_radar", "ADS-B radar",
       "square: big radar + labels; wide: radar left, list right; ultrawide/strip: compact radar + "
       "marquee; tall: radar on top, list below; tiny: radar only, bright targets", 60)
def adsb_radar(ctx: Ctx, img, k, n):
    R = ctx.rect
    if ctx.tier == "tiny" or min(ctx.w, ctx.h) < 10 and max(ctx.w, ctx.h) < 40:
        draw_radar(ctx, img, R.square(), k, n, False)
        return
    if ctx.h > ctx.w * 1.3:                              # portrait: stack
        top, rest = R.split_y(ctx.w)
        draw_radar(ctx, img, top, k, n, False)
        radar_info(ctx, img, rest.inset(0), k, n)
        return
    if ctx.aspect < 1.4:                                 # square-ish: radar fills, labels if big
        draw_radar(ctx, img, R.square(), k, n, ctx.t >= 3)
        if ctx.t >= 2 and ctx.t < 3:
            L.text(img, 0, 0, f"{len(AC)}", AMB)
        return
    left, rest = R.split_x(ctx.h, gap=2)                 # wide and wider: radar left, info right
    if rest.w < 14:
        draw_radar(ctx, img, R.square(), k, n, False)
        return
    draw_radar(ctx, img, left, k, n, False)
    radar_info(ctx, img, rest, k, n)


# ================================================================== 2. OrcSDR waterfall
SIGS = [(0.18, 0.9, 1), (0.37, 0.55, 2), (0.52, 1.0, 1), (0.71, 0.7, 3), (0.86, 0.45, 2)]


def _noise(i, j):
    v = (i * 73856093) ^ (j * 19349663)
    return ((v >> 7) & 255) / 255.0


def spectrum(x_frac, tau, n):
    v = 0.16 + 0.1 * _noise(int(x_frac * 997), tau % n)
    for c, a, m in SIGS:
        on = 0.5 + 0.5 * math.sin(TAU * m * tau / n + c * 9)
        v += a * on * math.exp(-((x_frac - c) / 0.018) ** 2)
    return min(1.0, v)


def wf_col(v):
    stops = [(0, (0, 0, 20)), (0.3, (0, 40, 140)), (0.55, (0, 170, 200)), (0.75, (240, 220, 40)), (1, (255, 60, 30))]
    for (a, ca), (b, cb) in zip(stops, stops[1:]):
        if v <= b:
            return lerp(ca, cb, (v - a) / (b - a))
    return stops[-1][1]


def draw_spec(img, R: Rect, k, n):
    if R.h <= 0 or R.w <= 0:
        return
    px = img.load()
    for i in range(R.w):
        v = spectrum((i + 0.5) / R.w, k, n)
        hgt = max(1, round(v * R.h))
        for y in range(R.h - hgt, R.h):
            px[R.x + i, R.y + y] = lerp(DOG, OG2, (R.h - y) / max(1, R.h)) if y > R.h - hgt else OG2


def draw_wf(img, R: Rect, k, n):
    px = img.load()
    for y in range(R.h):
        tau = k - y
        for i in range(R.w):
            px[R.x + i, R.y + y] = wf_col(spectrum((i + 0.5) / R.w, tau, n) * 0.95)


def smeter(img, R: Rect, k, n, vertical=False):
    lvl = 0.55 + 0.3 * math.sin(TAU * k / n) + 0.1 * math.sin(TAU * 3 * k / n)
    d = ImageDraw.Draw(img)
    if vertical:
        hh = round(lvl * R.h)
        d.rectangle([R.x, R.y, R.x + R.w - 1, R.y + R.h - 1], fill=(10, 14, 18))
        if hh:
            d.rectangle([R.x, R.y + R.h - hh, R.x + R.w - 1, R.y + R.h - 1], fill=OG if lvl < 0.8 else AMB)
    else:
        ww = round(lvl * R.w)
        d.rectangle([R.x, R.y, R.x + R.w - 1, R.y + R.h - 1], fill=(10, 14, 18))
        if ww:
            d.rectangle([R.x, R.y, R.x + ww - 1, R.y + R.h - 1], fill=OG if lvl < 0.8 else AMB)


@scene("adaptive_orcsdr_waterfall", "OrcSDR waterfall",
       "square: header + spectrum + waterfall; wide: spectrum/waterfall left, frequency + S-meter right; "
       "ultrawide/strip: spectrum over waterfall with a frequency tag when there is room; tiny: waterfall only", 48)
def orcsdr_waterfall(ctx: Ctx, img, k, n):
    R = ctx.rect
    if ctx.tier == "tiny":
        draw_wf(img, R, k, n)
        return
    freq, mode = "14.074", "FT8"
    if ctx.h > ctx.w * 1.3:                                         # portrait
        s = ctx.text_scale(1, Rect(0, 0, ctx.w, 12)) if ctx.w >= 40 else 1
        L.text(img, 0, 0, L.fit_text(freq, ctx.w, s), AMB, s)
        y = 6 * s + 1
        top, rest = Rect(0, y, ctx.w, ctx.h - y).split_y(max(4, (ctx.h - y) // 4))
        main, bot = rest.split_y(rest.h - 4)
        draw_spec(img, top, k, n)
        draw_wf(img, main, k, n)
        smeter(img, Rect(0, bot.y, ctx.w, max(1, bot.h)), k, n)
        return
    if ctx.aspect < 1.5:                                            # square
        y = 0
        if ctx.t >= 2:
            s = 2 if ctx.w >= 64 else 1
            x = L.text(img, 0, 0, freq, AMB, s)
            if x + L.text_w(mode) + 2 <= ctx.w:
                L.text(img, ctx.w - L.text_w(mode, 1), 0, mode, LPB)
            y = 6 * s + 1
        top, rest = Rect(0, y, ctx.w, ctx.h - y).split_y(max(4, (ctx.h - y) // 4))
        draw_spec(img, top, k, n)
        draw_wf(img, rest, k, n)
        return
    side_w = 0
    if ctx.aspect < 3.2 and ctx.w >= 48:                            # wide: info column
        side_w = max(24, ctx.w // 3)
    elif ctx.w >= 96 and ctx.h >= 12:                               # long: tag column
        side_w = L.text_w(freq) + 2
    main, side = R.split_x(ctx.w - side_w, gap=1 if side_w else 0) if side_w else (R, None)
    sh = max(3, min(main.h // 3, 12))
    top, rest = main.split_y(sh, gap=0)
    draw_spec(img, top, k, n)
    draw_wf(img, rest, k, n)
    if side is not None and side.w > 0:
        s = 2 if side.h >= 30 and side.w >= L.text_w(freq, 2) else 1
        L.text(img, side.x, side.y, L.fit_text(freq, side.w, s), AMB, s)
        y = side.y + 6 * s + 1
        if y + 5 <= side.y + side.h:
            L.text(img, side.x, y, mode, LPB)
            y += 7
        if y + 2 <= side.y + side.h:
            smeter(img, Rect(side.x, y, side.w - 1, min(4, side.y + side.h - y)), k, n)


# ================================================================== 3. ticker
TICK = [("BTC", "67210", 1.2), ("ETH", "3120", -0.8), ("SPX", "5712", 0.4), ("GOLD", "2398", 0.1), ("EUR", "1.094", -0.2)]


def series(i, m=24):
    return [math.sin(j / m * TAU * (1 + i % 2) + i) * 0.5 + 0.5 * math.sin(j / m * TAU * 3 + i * 2) * 0.3
            for j in range(m)]


def spark(img, R: Rect, vals, col):
    if R.w < 4 or R.h < 2:
        return
    lo, hi = min(vals), max(vals)
    d = ImageDraw.Draw(img)
    pts = []
    for i in range(R.w):
        v = vals[int(i * (len(vals) - 1) / max(1, R.w - 1))]
        pts.append((R.x + i, R.y + R.h - 1 - round((v - lo) / ((hi - lo) or 1) * (R.h - 1))))
    d.line(pts, fill=col)


def chg(c):
    return (f"+{c:.1f}%" if c >= 0 else f"{c:.1f}%"), (OG if c >= 0 else RED)


def _ticker_parts():
    parts = []
    for a, b, c in TICK:
        t, col = chg(c)
        parts += [(a, AMB), (b, CREAM), (t + "  ", col)]
    return parts


def _ticker_loop(ctx: Ctx) -> int:
    mode, s = _ticker_mode(ctx)
    if mode == "marquee":
        return max(24, math.ceil(strip_width(_ticker_parts(), s, 3 * s) / s))
    return 12 * len(TICK)


def _ticker_mode(ctx: Ctx):
    rows = (ctx.h + 1) // 6
    if ctx.tier == "tiny" or (ctx.aspect < 1.6 and (rows < 4 or ctx.w < 48)):
        return "single", 1
    if ctx.h < 24 or ctx.aspect >= 3:
        s = max(1, min(3, (ctx.h + 1) // 8)) if ctx.h >= 16 and ctx.aspect < 12 else 1
        return "marquee", s
    return "list", 2 if ctx.h >= 60 and ctx.w >= 96 else 1


@scene("adaptive_ticker", "Markets ticker",
       "ultrawide/strip: seamless marquee (+ sparkline row when tall enough); wide/square: list with "
       "sparklines, paged; tiny: one symbol at a time", _ticker_loop, 80)
def ticker(ctx: Ctx, img, k, n):
    mode, s = _ticker_mode(ctx)
    R = ctx.rect
    if mode == "marquee":
        parts = _ticker_parts()
        mh = 5 * s + 2
        sp_h = ctx.h - mh - 1
        top = Rect(0, 0 if sp_h < 4 else 0, ctx.w, mh if sp_h >= 4 else ctx.h)
        strip, step = marquee_strip(parts, s, 3 * s, n, top.h)
        draw_marquee(img, top, strip, step, k)
        if sp_h >= 4:                               # index line scrolling with the marquee
            R2 = Rect(0, mh + 1, ctx.w, sp_h)
            f = lambda u: 0.5 * math.sin(TAU * 2 * u) + 0.3 * math.sin(TAU * 5 * u + 1) + 0.15 * math.sin(TAU * 11 * u)  # noqa: E731
            pts = [(i, R2.y + R2.h - 1 - round((f(i / 128 + k / n) + 0.95) / 1.9 * (R2.h - 1))) for i in range(ctx.w)]
            ImageDraw.Draw(img).line(pts, fill=LPB)
        return
    if mode == "single":
        a, b, c = TICK[paged(k, n, len(TICK))]
        t, col = chg(c)
        sc = 2 if ctx.w >= 28 and ctx.h >= 22 else 1
        L.text(img, 0, 0, L.fit_text(a, ctx.w, sc), AMB, sc)
        y = 6 * sc
        if y + 5 <= ctx.h:
            L.text(img, 0, y, L.fit_text(t, ctx.w), col)
        if y + 11 <= ctx.h:
            spark(img, Rect(0, y + 6, ctx.w, ctx.h - y - 6), series(TICK.index((a, b, c))), LPB)
        return
    rh = 6 * s + 1
    rows = max(1, (ctx.h + 1) // rh)
    pages = math.ceil(len(TICK) / rows)
    while n % pages:
        pages += 1
    pg = paged(k, n, pages)
    for r, (a, b, c) in enumerate(TICK[pg * rows:(pg + 1) * rows]):
        y = r * rh
        t, col = chg(c)
        x = L.text(img, 0, y, a, AMB, s)
        tw = L.text_w(t, s)
        sw = ctx.w - (x + 2) - tw - 3
        if sw >= 10:
            vals = series(TICK.index((a, b, c)))
            spark(img, Rect(x + 2, y, sw, 5 * s), vals, dimc(LPB, 0.9))
        L.text(img, ctx.w - tw, y, t, col, s)


# ================================================================== 4. clock
def _hm(ctx):
    import time as _t
    tm = _t.gmtime(ctx.now)
    return tm.tm_hour, tm.tm_min, _t.strftime("%a %b %d", tm).upper().replace(" 0", " ")


def analog(img, R: Rect, k, h, m):
    d = ImageDraw.Draw(img)
    cx, cy = R.x + (R.w - 1) / 2, R.y + (R.h - 1) / 2
    r = (min(R.w, R.h) - 1) / 2
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=DPB)
    for i in range(12):
        a = TAU * i / 12
        if r >= 12:
            d.point((round(cx + (r - 2) * math.sin(a)), round(cy - (r - 2) * math.cos(a))), fill=LPB if i % 3 == 0 else GREY)
    ha = TAU * ((h % 12) + m / 60) / 12
    ma = TAU * m / 60
    sa = TAU * k / 60
    d.line([cx, cy, cx + 0.5 * r * math.sin(ha), cy - 0.5 * r * math.cos(ha)], fill=CREAM, width=2 if r >= 20 else 1)
    d.line([cx, cy, cx + 0.8 * r * math.sin(ma), cy - 0.8 * r * math.cos(ma)], fill=WHITE)
    d.line([cx, cy, cx + 0.9 * r * math.sin(sa), cy - 0.9 * r * math.cos(sa)], fill=AMB)


def sec_dot(img, R: Rect, k):
    d = ImageDraw.Draw(img)
    d.line([R.x, R.y, R.x + R.w - 1, R.y], fill=(14, 18, 24))
    x = R.x + round(k * (R.w - 1) / 59)
    for j, f in ((2, 0.25), (1, 0.5), (0, 1.0)):
        xx = R.x + (x - R.x - j) % R.w
        d.point((xx, R.y), fill=dimc(AMB, f))


@scene("adaptive_clock", "Clock",
       "square: analog face (+ digital below when large); wide: analog left, time + date right; "
       "ultrawide/strip: big digital + travelling seconds dot; tiny: digital only", 60, 1000)
def clock(ctx: Ctx, img, k, n):
    h, m, date = _hm(ctx)
    hm = f"{h:02d}:{m:02d}"
    R = ctx.rect
    if ctx.tier == "tiny" and ctx.aspect < 1.6:
        if L.text_w(hm) <= ctx.w:
            L.text(img, max(0, (ctx.w - L.text_w(hm)) // 2), max(0, (ctx.h - 5) // 2), hm, CREAM)
        else:                                            # stack HH over MM
            for i, part in enumerate((f"{h:02d}", f"{m:02d}")):
                L.text(img, (ctx.w - L.text_w(part)) // 2, max(0, (ctx.h - 12) // 2) + 6 * i, part,
                       CREAM if i == 0 else LPB)
        sec_dot(img, Rect(0, ctx.h - 1, ctx.w, 1), k)
        return
    if ctx.aspect < 1.5 or ctx.h > ctx.w:
        face = R.square() if ctx.h <= ctx.w * 1.3 else Rect(0, 0, ctx.w, ctx.w)
        if ctx.t >= 3 and ctx.h <= ctx.w * 1.3:
            face = Rect(face.x + 6, 0, face.w - 12, face.h - 12)
        analog(img, face, k, h, m)
        below = ctx.h - (face.y + face.h)
        if below >= 6:
            s = 2 if below >= 12 and ctx.w >= L.text_w(hm, 2) else 1
            L.text(img, (ctx.w - L.text_w(hm, s)) // 2, face.y + face.h + (below - 5 * s) // 2, hm, CREAM, s)
        return
    if ctx.aspect < 3.0:
        left, rest = R.split_x(ctx.h, gap=2)
        analog(img, left, k, h, m)
        s = max(1, min(4, rest.w // max(1, L.text_w(hm))))
        s = min(s, max(1, (rest.h - 7) // 5))
        L.text(img, rest.x, rest.y + 1, hm, CREAM, s)
        y = rest.y + 5 * s + 3
        if y + 5 <= ctx.h:
            L.text(img, rest.x, y, L.fit_text(date, rest.w), GREY)
        return
    s = max(1, min((ctx.h - 2) // 5, (ctx.w // 2) // max(1, L.text_w(hm))))
    x = L.text(img, 1, max(0, (ctx.h - 2 - 5 * s) // 2), hm, CREAM, s)
    if ctx.w - x >= L.text_w(date) + 2 and ctx.h >= 8:
        L.text(img, ctx.w - L.text_w(date) - 1, max(0, (ctx.h - 2 - 5) // 2), date, GREY)
    sec_dot(img, Rect(0, ctx.h - 1, ctx.w, 1), k)


# ================================================================== 5. Oregon Radio Co
WORDS = ["OREGON", "RADIO", "CO"]
BRAND = [OG, PB, AMB]


def brand_col(i, k, n):
    f = (i / 7.0 + k / n) % 1.0
    seg = f * 3
    a = int(seg) % 3
    return lerp(BRAND[a], BRAND[(a + 1) % 3], seg - int(seg))


@scene("adaptive_orc_marquee", "Oregon Radio Co",
       "ultrawide/strip: seamless brand marquee; square/portrait: stacked OREGON / RADIO / CO with a colour "
       "wave; tiny: ORC monogram", 48)
def orc_marquee(ctx: Ctx, img, k, n):
    if ctx.tier == "tiny" and ctx.aspect < 2:
        s = 2 if ctx.w >= 26 and ctx.h >= 12 else 1
        x = (ctx.w - L.text_w("ORC", s)) // 2
        for i, ch in enumerate("ORC"):
            x = L.text(img, x, (ctx.h - 5 * s) // 2, ch, brand_col(i, k, n), s)
        return
    stack_rows = 3
    s_stack = max(1, min((ctx.h + 1) // (6 * stack_rows), ctx.w // max(1, L.text_w("OREGON"))))
    s_line = max(1, min((ctx.h + 1) // 8, 4))
    one_line_fits = L.text_w("OREGON RADIO CO", s_line) <= ctx.w
    if ctx.aspect < 2.2 and s_stack >= 1 and 6 * s_stack * 3 <= ctx.h + 1 and not (ctx.aspect >= 1.8 and one_line_fits):
        y0 = (ctx.h + 1 - 6 * s_stack * 3) // 2
        i0 = 0
        for r, wd in enumerate(WORDS):
            x = (ctx.w - L.text_w(wd, s_stack)) // 2
            for j, ch in enumerate(wd):
                x = L.text(img, x, y0 + r * 6 * s_stack, ch, brand_col(i0 + j, k, n), s_stack)
            i0 += len(wd)
        return
    if one_line_fits and ctx.aspect < 5:
        x = (ctx.w - L.text_w("OREGON RADIO CO", s_line)) // 2
        for j, ch in enumerate("OREGON RADIO CO"):
            x = L.text(img, x, (ctx.h - 5 * s_line) // 2, ch, brand_col(j, k, n), s_line)
        return
    parts = [(ch, brand_col(j, 0, n)) for j, ch in enumerate("OREGON RADIO CO    ")]
    strip, step = marquee_strip(parts, s_line, 0, n, ctx.h)
    draw_marquee(img, ctx.rect, strip, step, k)


TEST_SIZES = [(16, 16), (64, 32), (256, 16), (96, 24)]                     # design target set (docs/DESIGN.md)
PREVIEW_SIZES = TEST_SIZES + [(64, 16), (64, 64), (128, 64), (128, 32), (128, 16), (32, 32), (32, 64), (96, 32)]
