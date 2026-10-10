"""Shared toolkit for the curated 64x64 preset set (tools/make_presets64.py).

Same brand palettes as the 64x16 generators (make_styles / make_personal_presets /
make_orco_presets), re-laid out for a square 64x64 panel. Every frame function is
fn(t, n) -> 64x64 RGB with motion a function of the phase t/n (integer cycles), so
each GIF is a seamless loop (orcpixels.loopmetric).
"""
from __future__ import annotations

import json
import math
import random
import sys
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import oregon_shape as OR  # noqa: E402
from make_styles import ICE_MAP, WF_MAP, LoopWaterfall, levels  # noqa: E402
from make_wave_small import FONT4x6, WORD_STOPS, orc_icon  # noqa: E402
from orcpixels.render import _lerp_stops  # noqa: E402
from pixfont import text35, width35  # noqa: E402

W = H = 64
TAU = 2 * math.pi
BLACK = (0, 0, 0)
# OrcSDR / personal palette
LIME, LIME2, DLIME = (140, 252, 40), (80, 255, 40), (30, 110, 10)
CYAN, DCYAN, WHITE = (90, 220, 255), (0, 70, 90), (225, 255, 255)
AMBER, RED, MAG = (255, 170, 20), (255, 50, 40), (255, 60, 200)
GRAD = [WHITE, CYAN, (110, 250, 140), (190, 252, 50)]
# Oregon Radio Co palette
OG, OG2, DOG, BODY = (40, 210, 90), (140, 245, 160), (12, 80, 34), (5, 28, 12)
PB, LPB, DPB = (30, 140, 255), (120, 200, 255), (0, 38, 90)
AMB, DAMB, CREAM = (255, 168, 20), (120, 64, 0), (255, 236, 190)
UNI = ImageFont.truetype(str(ROOT / "orcpixels/fonts/unifont.otf"), 16)

REGISTRY: dict[str, dict] = {}


def p64(name, n=24, ms=90, note="", colors=256):
    """Register the 64x64 version of preset `name` (meta copied from presets/<name>.json)."""
    def deco(fn):
        REGISTRY[name] = dict(name=name, n=n, ms=ms, fn=fn, note=note, colors=colors)
        return fn
    return deco


# ------------------------------------------------------------------ basics
def canvas(bg=BLACK):
    return Image.new("RGB", (W, H), bg)


def dim(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def mix(a, b, k):
    k = max(0.0, min(1.0, k))
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def put(img, x, y, c):
    x, y = int(round(x)), int(round(y))
    if 0 <= x < img.width and 0 <= y < img.height:
        img.putpixel((x, y), c)


def grad(x0, w, stops=GRAD):
    return lambda gx, gy: _lerp_stops(stops, (gx - x0) / max(1, w - 1))


def vgrad(y0, h, stops):
    return lambda gx, gy: _lerp_stops(stops, (gy - y0) / max(1, h - 1))


def paint_mask(img, mask, x, y, color):
    """Paste color (tuple or fn(gx, gy)) through an L mask placed at (x, y)."""
    x, y = int(x), int(y)
    if callable(color):
        px, mp = img.load(), mask.load()
        for my in range(mask.height):
            for mx in range(mask.width):
                if mp[mx, my]:
                    gx, gy = x + mx, y + my
                    if 0 <= gx < img.width and 0 <= gy < img.height:
                        px[gx, gy] = color(gx, gy)
    else:
        img.paste(Image.new("RGB", mask.size, color), (x, y), mask)


@lru_cache(maxsize=1024)
def uni_mask(s: str, scale: int = 1) -> Image.Image:
    w = max(1, int(UNI.getlength(s)))
    m = Image.new("L", (w, 16))
    ImageDraw.Draw(m).text((0, 0), s, fill=255, font=UNI)
    m = m.point(lambda p: 255 if p > 128 else 0)
    if scale > 1:
        m = m.resize((w * scale, 16 * scale), Image.Resampling.NEAREST)
    return m


def uw(s, scale=1):
    return int(UNI.getlength(s)) * scale


def uni(img, x, y, s, color, scale=1) -> int:
    m = uni_mask(s, scale)
    paint_mask(img, m, x, y, color)
    return int(x) + m.width


def unic(img, y, s, color, scale=1, x0=0, x1=63):
    return uni(img, x0 + (x1 - x0 + 1 - uw(s, scale)) // 2, y, s, color, scale)


def t35(img, x, y, s, color) -> int:
    return text35(img, int(x), int(y), s, color)


def t35c(img, y, s, color, x0=0, x1=63):
    return text35(img, x0 + (x1 - x0 + 1 - width35(s)) // 2, int(y), s, color)


def f46_mask(s: str, scale: int = 1) -> Image.Image:
    """The OrcSDR 4x6 wordmark font (letters O r c S D R) as an L mask."""
    w = len(s) * 5 - 1
    m = Image.new("L", (w, 6))
    for i, ch in enumerate(s):
        for y, row in enumerate(FONT4x6[ch]):
            for x, c in enumerate(row):
                if c == "#":
                    m.putpixel((i * 5 + x, y), 255)
    if scale > 1:
        m = m.resize((w * scale, 6 * scale), Image.Resampling.NEAREST)
    return m


def orcsdr_word(img, x, y, scale=2, stops=WORD_STOPS):
    m = f46_mask("OrcSDR", scale)
    paint_mask(img, m, x, y, grad(x, m.width, stops))
    return m.width


@lru_cache(maxsize=8)
def _orc(scale: int) -> Image.Image:
    ic = orc_icon()
    return ic.resize((16 * scale, 16 * scale), Image.Resampling.NEAREST) if scale > 1 else ic


def orc(img, x, y, scale=2):
    ic = _orc(scale)
    img.paste(ic, (int(x), int(y)), ic.convert("L").point(lambda v: 255 if v else 0))


def ring(d, cx, cy, r, color, width=1):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color, width=width)


def scroll(strip: Image.Image, off: int, x=0, y=0, w=W, into=None):
    """Paste a horizontally wrapping window of `strip` at offset off into `into`."""
    sw = strip.width
    off %= sw
    reps = (off + w) // sw + 1
    tiled = Image.new("RGB", (sw * reps, strip.height))
    for i in range(reps):
        tiled.paste(strip, (i * sw, 0))
    win = tiled.crop((off, 0, off + w, strip.height))
    if into is not None:
        into.paste(win, (x, y), win.convert("L").point(lambda v: 255 if v else 0))
    return win


def stars(seed, count, w=W, h=H):
    r = random.Random(seed)
    return [(r.randrange(w), r.randrange(h), r.random()) for _ in range(count)]


def twinkle(img, pts, t, n, base=(150, 160, 190)):
    for i, (x, y, ph) in enumerate(pts):
        k = 0.45 + 0.55 * (0.5 + 0.5 * math.cos(TAU * (t / n * (1 + i % 2) + ph)))
        put(img, x, y, dim(base, k))


# ------------------------------------------------------------------ Oregon
@lru_cache(maxsize=8)
def oregon(h: int):
    """(fill, outline, interior) L masks of Oregon at height h."""
    fm, ol = OR.mask(h), OR.outline(h)
    inner = Image.new("L", fm.size)
    inner.paste(fm, (0, 0), ol.point(lambda v: 255 - v))
    return fm, ol, inner


def city(name, h, x0=0, y0=0):
    x, y = OR.project(*OR.CITIES[name], h)
    return x + x0, y + y0


def tower(img, x, y, h=10, color=(150, 160, 170), lamp=None):
    d = ImageDraw.Draw(img)
    d.line((x, y, x, y - h), fill=color)
    for k in range(0, h, 3):
        w = max(1, (h - k) // 4)
        d.line((x - w, y - k, x + w, y - k), fill=dim(color, 0.6))
    d.line((x - h // 3, y, x, y - h), fill=dim(color, 0.8))
    d.line((x + h // 3, y, x, y - h), fill=dim(color, 0.8))
    if lamp:
        put(img, x, y - h - 1, lamp)


# ------------------------------------------------------------------ RF art
def waterfall(w, h, n, seed=1, carriers=None, stops=WF_MAP, q=8):
    lw = LoopWaterfall(w, n, n, seed=seed, **({"carriers": carriers} if carriers else {}))

    def img_at(t, down=True):
        rows = [lw.hist[(t - y) % n] for y in range(h)] if down else [lw.hist[(t - (h - 1 - y)) % n] for y in range(h)]
        im = Image.new("RGB", (w, h))
        px = im.load()
        for y, row in enumerate(rows):
            for x, v in enumerate(row):
                px[x, y] = _lerp_stops(stops, round(max(0.0, min(1.0, v)) * q) / q)
        return im
    return img_at


def spectrum(img, x0, y0, w, h, lv, stops=None, every=1, bright=None, fill=0.45):
    stops = stops or [(40, 200, 90), (60, 240, 130), (70, 230, 210), (90, 220, 255)]
    d = ImageDraw.Draw(img)
    for i in range(0, w, every):
        v = lv[min(len(lv) - 1, i * len(lv) // w)]
        hh = max(1, round(v * h))
        c = _lerp_stops(stops, i / max(1, w - 1))
        d.line((x0 + i, y0 + h - 1, x0 + i, y0 + h - hh), fill=dim(c, fill))
        put(img, x0 + i, y0 + h - hh, bright or c)


def lv_anim(t, n, w, seed=3, amt=0.18):
    """Banner spectrum with smooth looping jitter."""
    base = levels(w)
    r = random.Random(seed)
    ph = [r.random() for _ in range(w)]
    fr = [1 + r.randrange(2) for _ in range(w)]
    return [max(0.04, min(1.0, v + amt * (0.4 + v) * math.sin(TAU * (fr[i] * t / n + ph[i])))) for i, v in enumerate(base)]


def frame_box(img, col, inset=0):
    d = ImageDraw.Draw(img)
    d.rectangle((inset, inset, 63 - inset, 63 - inset), outline=col)


def family_bar(img, y, cols, t=0, n=1, speed=1):
    """A thin moving brand stripe (seamless): colours cycle along x."""
    k = len(cols)
    for x in range(64):
        c = cols[int((x / 8 + speed * k * t / n)) % k]
        put(img, x, y, c)


def load_meta(name: str) -> dict:
    return json.loads((ROOT / "presets" / f"{name}.json").read_text(encoding="utf-8"))
