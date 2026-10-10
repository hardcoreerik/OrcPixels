"""64x64 OrcSDR family: splash, orc + wordmark frames, the 10 OrcSDR styles."""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw

from p64lib import (BLACK, CYAN, DCYAN, DLIME, GRAD, ICE_MAP, LIME, LIME2, TAU, WF_MAP, WHITE, WORD_STOPS,
                    canvas, dim, f46_mask, grad, levels, lv_anim, mix, orc, orcsdr_word, p64, paint_mask, put,
                    ring, scroll, spectrum, t35, t35c, uni, uni_mask, unic, uw, waterfall)
from orcpixels.render import _lerp_stops

SPLASH_STOPS = [(80, 255, 40), (140, 255, 120), (80, 255, 40)]


# ------------------------------------------------------------------ splash
def _splash_strip():
    txt = "OrcPixels"
    w = uw(txt) + 24
    w += (-w) % 4
    s = Image.new("RGB", (w, 16))
    uni(s, 0, 0, txt, grad(0, uw(txt), SPLASH_STOPS))
    return s


@p64("orcpixels_splash", n=None, ms=80, note="orc 2x centred + 'OrcPixels' marquee, octagon frame")
def splash(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    oc = [(18, 2), (45, 2), (55, 12), (55, 36), (45, 46), (18, 46), (8, 36), (8, 12)]
    d.polygon(oc, outline=(25, 110, 25))
    orc(f, 16, 8, 2)
    s = _splash_strip()
    scroll(s, 4 * t, 0, 47, 64, f)
    for x in range(64):
        if (x + 4 * t) % 8 == 0:
            put(f, x, 63, DLIME)
    return f


splash_n = _splash_strip().width // 4


# ------------------------------------------------------------------ stills
@p64("orcsdr_orc", n=1, note="orc 2x + 4x6 wordmark at 2x, banner spectrum underline")
def orcsdr_orc(t, n):
    f = canvas()
    orc(f, 16, 2, 2)
    orcsdr_word(f, 3, 38, 2)
    lv = levels(64)
    for x in range(0, 64, 2):
        h = max(1, round(lv[x] * 8))
        c = _lerp_stops(GRAD[1:], x / 63)
        ImageDraw.Draw(f).line((x, 63, x, 64 - h), fill=dim(c, 0.6))
        put(f, x, 64 - h, c)
    return f


def _wave(t, n, word="2x", anim=False, x_word=None):
    f = canvas()
    d = ImageDraw.Draw(f)
    oc = [(18, 0), (45, 0), (49, 4), (49, 29), (45, 33), (18, 33), (14, 29), (14, 4)]
    d.polygon(oc, outline=(25, 110, 25))
    orc(f, 16, 1, 2)
    if word == "2x":
        orcsdr_word(f, 3 if x_word is None else x_word, 36, 2)
        top = 50
    else:                                     # v1: centred 3x5 wordmark, taller spectrum
        t35c(f, 37, "ORCSDR", grad(20, 24, WORD_STOPS))
        top = 44
    lv = lv_anim(t, n, 64, seed=7, amt=0.16) if anim else levels(64)
    spectrum(f, 0, top, 64, 64 - top, lv, every=2)
    return f


@p64("orcsdr_wave", n=1, note="orc 2x in the octagon + wordmark + full-width banner spectrum")
def orcsdr_wave(t, n):
    f = canvas()
    orc(f, 0, 0, 2)
    m = f46_mask("Orc", 2)
    paint_mask(f, m, 34, 4, grad(34, 28, WORD_STOPS[:2]))
    m = f46_mask("SDR", 2)
    paint_mask(f, m, 34, 18, grad(34, 28, WORD_STOPS[2:]))
    spectrum(f, 0, 36, 64, 28, levels(64), every=1)
    return f


@p64("orcsdr_wave_small", n=1, note="banner-faithful v2: octagon orc, 2x wordmark, spectrum")
def ws(t, n):
    return _wave(t, n)


@p64("orcsdr_wave_small_v2", n=1, note="v2 backup: wordmark at x=2")
def ws2(t, n):
    return _wave(t, n, x_word=2)


@p64("orcsdr_wave_small_v1", n=1, note="v1 backup: centred 3x5 wordmark, taller spectrum")
def ws1(t, n):
    return _wave(t, n, word="35")


@p64("orcsdr_wave_small_anim", n=16, ms=110, note="v2 layout with a breathing spectrum")
def wsa(t, n):
    return _wave(t, n, anim=True)


@p64("orcsdr_wave_small_anim_v2", n=16, ms=110, note="v2 backup, animated")
def wsa2(t, n):
    return _wave(t, n, anim=True, x_word=2)


@p64("orcsdr_wave_small_anim_v1", n=16, ms=110, note="v1 backup, animated")
def wsa1(t, n):
    return _wave(t, n, word="35", anim=True)


# ------------------------------------------------------------------ the 10 styles
def _word_paint(scale=2, font_rows=None):
    m = f46_mask("OrcSDR", scale)
    rows = {}
    for y in range(m.height):
        rows[y] = [1 if m.getpixel((x, y)) else 0 for x in range(m.width)]
    return rows, m.width


def _wf_word(n, h, seed, stops, down=True):
    """Waterfall whose history carries the 2x wordmark; 2 rows per frame."""
    from make_styles import LoopWaterfall
    rows, ww = _word_paint(2)
    hist_n = 2 * n
    paint = {}
    for y, r in rows.items():
        k = (hist_n // 2 + (len(rows) - 1 - y if down else y)) % hist_n
        paint[k] = r
    lw = LoopWaterfall(64, hist_n, hist_n, seed=seed, paint=paint, paint_off=(64 - ww) // 2)

    def at(t):
        im = Image.new("RGB", (64, h))
        px = im.load()
        for y in range(h):
            k = (2 * t - y) % hist_n if down else (2 * t - (h - 1 - y)) % hist_n
            for x, v in enumerate(lw.hist[k]):
                px[x, y] = _lerp_stops(stops, round(min(1.0, v) * 4) / 4)
        return im
    return at


_S01 = {}


@p64("orcsdr_style01", n=32, ms=90, note="full-height waterfall; 'OrcSDR' painted as signal energy scrolls down")
def s01(t, n):
    if "wf" not in _S01:
        _S01["wf"] = _wf_word(n, 64, 11, WF_MAP, down=True)
    return _S01["wf"](t)


_S07 = {}


@p64("orcsdr_style07", n=32, ms=90, note="ICE waterfall, the wordmark rises")
def s07(t, n):
    if "wf" not in _S07:
        _S07["wf"] = _wf_word(n, 64, 17, ICE_MAP, down=False)
    return _S07["wf"](t)


_S02 = {}


@p64("orcsdr_style02", n=20, ms=110, note="orc 2x + wordmark header over a 30-row waterfall")
def s02(t, n):
    if "wf" not in _S02:
        _S02["wf"] = waterfall(64, 30, n, seed=4, q=4)
    f = canvas()
    orc(f, 0, 0, 2)
    orcsdr_word(f, 34, 6, 1)
    t35(f, 34, 16, "SDR RX", dim(CYAN, 0.7))
    put(f, 60, 16, LIME if t % 4 < 2 else DLIME)
    f.paste(_S02["wf"](t), (0, 34))
    return f


@p64("orcsdr_style03", n=24, ms=90, note="full-field spectrum with peak hold under orc + wordmark")
def s03(t, n):
    f = canvas()
    orc(f, 0, 0, 1)
    orcsdr_word(f, 20, 5, 1)
    lv = lv_anim(t, n, 64, seed=3, amt=0.25)
    peaks = [max(lv_anim((t - j) % n, n, 64, seed=3, amt=0.25)[i] for j in range(5)) for i in range(64)]
    d = ImageDraw.Draw(f)
    for x in range(0, 64, 2):
        h = max(1, round(lv[x] * 44))
        c = _lerp_stops([(40, 200, 90), (60, 240, 130), (70, 230, 210), (90, 220, 255)], x / 63)
        d.line((x, 63, x, 64 - h), fill=dim(c, 0.45))
        put(f, x, 64 - h, c)
        ph = round(peaks[x] * 44)
        if ph > h:
            put(f, x, 64 - ph, WHITE)
    return f


@p64("orcsdr_style04", n=16, ms=80, note="spectrum + lime scan cursor sweeping, readout follows")
def s04(t, n):
    f = canvas()
    orcsdr_word(f, 3, 2, 2)
    lv = levels(64)
    spectrum(f, 0, 22, 64, 34, lv, every=2)
    cx = int(round((0.5 - 0.5 * math.cos(TAU * t / n)) * 63))
    d = ImageDraw.Draw(f)
    d.line((cx, 18, cx, 57), fill=LIME)
    for x in (cx - 1, cx + 1):
        for y in range(18, 58, 2):
            put(f, x, y, DLIME)
    mhz = 88 + cx * 20 / 63
    t35c(f, 59, f"{mhz:.1f} MHZ", dim(CYAN, 0.9))
    return f


@p64("orcsdr_style05", n=12, ms=110, note="2x unifont 'Orc'/'SDR' with a cyan->lime pulse")
def s05(t, n):
    f = canvas()
    k = 0.5 + 0.5 * math.cos(TAU * t / n)
    stops = [mix(CYAN, WHITE, k), mix(LIME, CYAN, 1 - k)]
    unic(f, 0, "Orc", grad(8, 48, stops), 2)
    unic(f, 30, "SDR", grad(8, 48, stops[::-1]), 2)
    for x in range(64):
        if (x + t * 16 // n * 0) % 2 == 0:
            put(f, x, 62, dim(LIME, 0.25 + 0.5 * k))
    return f


@p64("orcsdr_style06", n=22, ms=90, note="matrix rain over the full panel revealing the 2x wordmark")
def s06(t, n):
    f = canvas()
    r = random.Random(6)
    cols = [(x, r.randrange(64), 1 + r.randrange(2), 4 + r.randrange(8)) for x in range(0, 64, 2)]
    for x, y0, sp, ln in cols:
        sp = 64 // n * 0 + sp
        head = (y0 + sp * t * 64 // n) % 64 if False else (y0 + round(sp * 64 * t / n)) % 64
        for j in range(ln):
            y = (head - j) % 64
            put(f, x, y, WHITE if j == 0 else dim(LIME2, 0.9 - j / ln))
    m = f46_mask("OrcSDR", 2)
    box = (3, 26, 3 + m.width, 26 + m.height)
    ImageDraw.Draw(f).rectangle((box[0] - 2, box[1] - 2, box[2] + 1, box[3] + 1), fill=BLACK)
    paint_mask(f, m, 3, 26, grad(3, m.width, [WHITE, CYAN, LIME]))
    return f


@p64("orcsdr_style08", n=16, ms=80, note="retro scope: dotted graticule, AM-modulated lime trace")
def s08(t, n):
    f = canvas()
    for x in range(0, 64, 8):
        for y in range(0, 64, 2):
            put(f, x, y, (18, 40, 18))
    for y in range(0, 64, 8):
        for x in range(0, 64, 2):
            put(f, x, y, (18, 40, 18))
    last = None
    d = ImageDraw.Draw(f)
    for x in range(64):
        env = 0.55 + 0.45 * math.sin(TAU * (x / 64 - t / n))
        y = 32 - 24 * env * math.sin(TAU * (x * 6 / 64))
        p = (x, y)
        if last:
            d.line((last, p), fill=LIME)
        last = p
    orcsdr_word(f, 34, 57, 1)
    return f


@p64("orcsdr_style09", n=24, ms=100, note="big analog-style S-meter: segmented bar + needle, orc + wordmark")
def s09(t, n):
    f = canvas()
    orc(f, 0, 0, 1)
    orcsdr_word(f, 20, 5, 1)
    lvl = 0.55 + 0.35 * math.sin(TAU * t / n) + 0.1 * math.sin(TAU * 3 * t / n)
    d = ImageDraw.Draw(f)
    cx, cy, r = 32, 58, 30
    for i in range(16):
        a = math.pi * (1 - i / 15)
        on = i / 15 <= lvl
        c = LIME if i < 10 else (255, 200, 40) if i < 13 else (255, 60, 40)
        x0, y0 = cx + (r - 6) * math.cos(a), cy - (r - 6) * math.sin(a)
        x1, y1 = cx + r * math.cos(a), cy - r * math.sin(a)
        d.line((x0, y0, x1, y1), fill=c if on else dim(c, 0.2))
    a = math.pi * (1 - min(1, lvl))
    d.line((cx, cy, cx + (r - 8) * math.cos(a), cy - (r - 8) * math.sin(a)), fill=WHITE)
    put(f, cx, cy, CYAN)
    s = min(9, int(lvl * 9) + 1)
    t35(f, 0, 58, f"S{s}", CYAN)
    return f


_S10 = {}


@p64("orcsdr_style10", n=20, ms=110, note="classic panadapter (rows 0-25) over a waterfall")
def s10(t, n):
    if "wf" not in _S10:
        _S10["wf"] = waterfall(64, 36, n, seed=9, q=4)
    f = canvas()
    lv = lv_anim(t, n, 64, seed=9, amt=0.2)
    spectrum(f, 0, 1, 64, 24, lv, every=1)
    for x in range(0, 64, 2):
        put(f, x, 26, (20, 50, 20))
    put(f, 31, 0, LIME)
    put(f, 32, 0, LIME)
    f.paste(_S10["wf"](t), (0, 28))
    return f


def setup():
    from p64lib import REGISTRY
    REGISTRY["orcpixels_splash"]["n"] = splash_n
