"""64x64 versions of the 30 Oregon Radio Co presets (family orc_co).

Same palette as make_orco_presets (Oregon green, Pacific blue, amber, cream), re-laid out
for the square panel. Every frame is fn(t, n) with motion periodic in t/n -> seamless loops.
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw

from p64lib import (AMB, BLACK, BODY, CREAM, DAMB, DOG, DPB, LPB, OG, OG2, PB, TAU, WHITE, WF_MAP,
                    canvas, city, dim, grad, mix, oregon, orc, p64, paint_mask, put, ring, scroll,
                    stars, t35, t35c, tower, twinkle, uni, uni_mask, unic, uw, waterfall, width35, lv_anim,
                    spectrum)
import oregon_shape as OR
from orcpixels.render import _lerp_stops

RED = (255, 50, 40)
BRAND = [OG, PB, AMB]


def tri(x):
    """0..1..0 triangle over one period of x in [0,1)."""
    x %= 1.0
    return 2 * x if x < 0.5 else 2 - 2 * x


def border_pts(h):
    """Largest Oregon ring projected to float pixel coords at height h."""
    lon0, lat1, s, k, w = OR._geom(h)
    ring_ = max(OR.rings(), key=len)
    return [((lon - lon0) * k, (lat1 - lat) * s) for lon, lat in ring_]


def resample(pts, m):
    seg = [math.dist(pts[i], pts[(i + 1) % len(pts)]) for i in range(len(pts))]
    tot = sum(seg)
    out, i, acc = [], 0, 0.0
    for j in range(m):
        tgt = tot * j / m
        while acc + seg[i] < tgt:
            acc += seg[i]
            i += 1
        u = (tgt - acc) / seg[i] if seg[i] else 0
        a, b = pts[i], pts[(i + 1) % len(pts)]
        out.append((a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u))
    return out


def state(f, x, y, h, fill=BODY, line=OG):
    fm, ol, inner = oregon(h)
    if fill:
        paint_mask(f, inner, x, y, fill)
    if line:
        paint_mask(f, ol, x, y, line)


def brand_grad(x0, w):
    return grad(x0, w, [OG, PB, AMB])


# ---------------------------------------------------------------- 1 acronym
_ACR = [("O", "REGON"), ("R", "ADIO"), ("C", "OMPANY")]


@p64("p_orco_acronym", n=48, ms=90, note="big O/R/C column, the words type out beside it, hold, erase")
def acronym(t, n):
    f = canvas()
    total = sum(len(w) for _, w in _ACR)               # 15
    k = t if t < total else total if t < n - total else n - t   # type / hold / erase
    left = k
    for i, (L, word) in enumerate(_ACR):
        y = 3 + i * 18
        uni(f, 2, y, L, AMB)
        shown = word[:max(0, min(len(word), left))]
        x = uni(f, 11, y, shown, OG2) if shown else 11
        if 0 <= left < len(word) or (left == len(word) and i == 2):
            if (t // 3) % 2 == 0 or t < total or t >= n - total:
                ImageDraw.Draw(f).rectangle((x + 1, y + 13, x + 6, y + 14), fill=CREAM)
        left -= len(word)
    ImageDraw.Draw(f).line((0, 58, 63, 58), fill=DOG)
    t35c(f, 60, "THEORC.DEV", dim(PB, 0.9))
    return f


# ---------------------------------------------------------------- 2 badge
@p64("p_orco_badge", n=32, ms=90, note="EST. 2026 badge, big amber ORC, Oregon seal, one glint per loop")
def badge(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rounded_rectangle((1, 1, 62, 62), radius=6, outline=AMB)
    d.rounded_rectangle((3, 3, 60, 60), radius=5, outline=DAMB)
    t35c(f, 6, "EST.", CREAM)
    unic(f, 12, "ORC", AMB, 2)
    state(f, 32 - 16, 44, 14, fill=DOG, line=OG2)
    t35(f, 6, 52, "20", CREAM)
    t35(f, 50, 52, "26", CREAM)
    s = -24 + (64 + 64 + 48) * t / (n - 8) if t < n - 8 else 999      # sweep, then rest off-panel
    px = f.load()
    for y in range(64):
        for x in range(64):
            dd = (x + y) - s
            if 0 <= dd < 6 and px[x, y] != BLACK:
                px[x, y] = mix(px[x, y], WHITE, 0.75 - 0.1 * dd)
    return f


# ---------------------------------------------------------------- 3 cities
_CITY3 = [("Portland", "PORTLAND", "97.1"), ("Eugene", "EUGENE", "88.5"), ("Bend", "BEND", "101.7")]


@p64("p_orco_cities", n=36, ms=100, note="Portland, Eugene, Bend take turns on air: ring, packet hop, name + freq")
def cities(t, n):
    f = canvas()
    ox, oy, h = 4, 4, 40
    state(f, ox, oy, h)
    d = ImageDraw.Draw(f)
    seg = n // 3
    tt = (t + seg // 2) % n                   # loop seam falls mid-pulse, not on a hand-over
    i, u = tt // seg, (tt % seg) / seg
    pts = [city(c, h, ox, oy) for c, _, _ in _CITY3]
    for j, (x, y) in enumerate(pts):
        d.rectangle((x - 1, y - 1, x + 1, y + 1), fill=AMB if j == i else dim(OG2, 0.6))
    x, y = pts[i]
    if u < 0.6:
        r = 2 + u / 0.6 * 12
        ring(d, x, y, r, dim(AMB, 1 - u / 0.7))
        ring(d, x, y, r * 0.5, dim(AMB, 0.9 - u / 0.7))
    else:
        v = (u - 0.6) / 0.4
        x2, y2 = pts[(i + 1) % 3]
        put(f, round(x + (x2 - x) * v), round(y + (y2 - y) * v), CREAM)
    _, name, fq = _CITY3[i]
    t35c(f, 50, name, OG2)
    t35c(f, 57, fq + " FM", AMB)
    return f


# ---------------------------------------------------------------- 4 coast
@p64("p_orco_coast", n=16, ms=120, note="waves roll onto a headland lighthouse; beam blinks, radio arcs + OREGON COAST")
def coast(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    ph = TAU * t / n
    for y in range(40, 64):
        for x in range(64):
            v = math.sin(x * 0.35 + ph * 2 - y * 0.9) * 0.5 + 0.5
            c = mix(DPB, PB, v * (y - 38) / 26)
            if v > 0.92:
                c = LPB
            put(f, x, y, c)
    d.polygon([(40, 63), (46, 40), (52, 34), (63, 33), (63, 63)], fill=DOG)
    d.polygon([(44, 63), (48, 44), (63, 42), (63, 63)], fill=BODY)
    d.rectangle((54, 20, 57, 33), fill=CREAM)
    d.rectangle((54, 24, 57, 25), fill=RED)
    d.rectangle((54, 29, 57, 30), fill=RED)
    d.rectangle((53, 17, 58, 19), fill=(60, 60, 60))
    on = (t // 4) % 2 == 0 if n > 16 else (t // 2) % 2 == 0
    put(f, 55, 18, AMB if on else DAMB)
    put(f, 56, 18, AMB if on else DAMB)
    if on:
        d.polygon([(54, 18), (36, 13), (36, 23)], fill=dim(AMB, 0.25))
    for k in range(3):
        r = 6 + ((t / n * 2 + k / 3) % 1) * 30
        d.arc((55 - r, 18 - r, 55 + r, 18 + r), 200, 250, fill=dim(OG2, 1 - r / 40))
    t35c(f, 2, "OREGON", OG2, 0, 40)
    t35c(f, 9, "COAST", LPB, 0, 40)
    return f


# ---------------------------------------------------------------- 5 columbia
@p64("p_orco_columbia", n=16, ms=110, note="Columbia flows west under the gorge title, amber packets ride the current")
def columbia(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    t35c(f, 1, "COLUMBIA", OG2)
    t35c(f, 8, "GORGE", dim(CREAM, 0.8))
    off = 64 * t / n
    for x in range(64):
        top = 24 + round(3 * math.sin(TAU * x / 64))
        bot = 46 + round(3 * math.sin(TAU * x / 32 + 1))
        for y in range(15, top):
            put(f, x, y, OG if y > top - 3 else (DOG if (x + y) % 5 else OG))
        for y in range(bot, 58):
            put(f, x, y, OG if y < bot + 2 else (DOG if (x * 3 + y) % 7 else OG))
        for y in range(top, bot):
            v = 0.5 + 0.5 * math.sin(TAU * ((x + off) / 16) + y * 1.3)
            put(f, x, y, mix(DPB, PB, v * 0.8))
    for k, (lane, ph) in enumerate([(30, 0), (35, 0.33), (40, 0.66)]):
        for rep in range(2):
            x = int((ph * 64 + rep * 32 - off * (1 + k % 2)) % 64)
            d.rectangle((x, lane, x + 2, lane + 1), fill=AMB)
    t35c(f, 59, "DATA RIVER", AMB)
    return f


# ---------------------------------------------------------------- 6 crater
@p64("p_orco_crater", n=24, ms=110, note="caldera rim, deep blue lake, Wizard Island, ripples + CRATER LAKE 1949 FT")
def crater(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.ellipse((2, 6, 61, 46), fill=(70, 60, 50))
    d.ellipse((5, 9, 58, 43), fill=(0, 30, 90))
    d.ellipse((8, 12, 55, 40), fill=(0, 45, 130))
    for k in range(3):
        r = ((t / n) + k / 3) % 1 * 20
        if r > 1:
            d.ellipse((38 - r * 1.3, 28 - r * 0.6, 38 + r * 1.3, 28 + r * 0.6), outline=dim(LPB, 1 - r / 22))
    d.polygon([(14, 30), (18, 24), (22, 30)], fill=OG)
    put(f, 18, 24, CREAM)
    for x in range(2, 62, 3):
        put(f, x, 7 + (x * 7) % 3, dim(CREAM, 0.7))
    t35c(f, 50, "CRATER LAKE", LPB)
    t35c(f, 57, "1949 FT", CREAM)
    return f


# ---------------------------------------------------------------- 7 dial
@p64("p_orco_dial", n=32, ms=90, note="FM dial 88-108, amber needle sweeps; ORC FM + signal bars light at 97.1")
def dial(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rectangle((1, 20, 62, 42), outline=DAMB, fill=(20, 12, 0))
    for i, mhz in enumerate(range(88, 109)):
        x = 4 + round(i * 56 / 20)
        d.line((x, 34, x, 37 if mhz % 2 else 39), fill=dim(CREAM, 0.6))
    for mhz in (88, 98, 108):
        x = 4 + round((mhz - 88) * 56 / 20)
        s = str(mhz)
        t35(f, max(2, min(61 - width35(s), x - width35(s) // 2)), 25, s, CREAM)
    fq = 98 - 10 * math.cos(TAU * t / n)
    x = 4 + (fq - 88) * 56 / 20
    d.line((round(x), 22, round(x), 41), fill=AMB)
    lock = max(0.0, 1 - abs(fq - 97.1) / 3)
    unic(f, 2, "ORC FM", mix(dim(OG, 0.2), OG2, lock))
    for b in range(6):
        hh = 3 + b * 2
        lit = lock * 6 > b
        d.rectangle((14 + b * 6, 62 - hh, 17 + b * 6, 62), fill=OG2 if lit else DOG)
    t35(f, 52, 48, "97", AMB)
    t35(f, 52, 55, ".1", AMB)
    return f


# ---------------------------------------------------------------- 8 dongle
_BITS = "1011001110001011010011100101"


@p64("p_orco_dongle", n=28, ms=90, note="SDR dongle with blinking LEDs streaming bits to TheOrc.dev, 2.4 MSPS")
def dongle(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rounded_rectangle((4, 22, 30, 34), radius=2, fill=(40, 44, 50), outline=(120, 130, 140))
    d.rectangle((30, 25, 36, 31), fill=(150, 150, 160))
    d.line((4, 28, 0, 28), fill=(150, 150, 160))
    t35(f, 7, 26, "RTL", WHITE)
    put(f, 25, 25, RED if t % 4 < 2 else dim(RED, 0.2))
    put(f, 25, 30, OG2 if t % 2 == 0 else DOG)
    strip = Image.new("RGB", (len(_BITS) * 4, 6))
    for i, b in enumerate(_BITS):
        t35(strip, i * 4, 0, b, AMB if b == "1" else DAMB)
    win = scroll(strip, -4 * t, 0, 0, 26)
    f.paste(win, (38, 26))
    t35c(f, 4, "SDR DONGLE", OG2)
    t35c(f, 46, "THEORC.DEV", LPB)
    t35c(f, 55, "2.4 MSPS", AMB)
    return f


# ---------------------------------------------------------------- 9 eq
@p64("p_orco_eq", n=16, ms=110, note="OREGON / RADIO CO wordmark over 16 bouncing EQ bars, letters brighten with the bars")
def eq(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    hs = []
    for i in range(16):
        v = 0.5 + 0.3 * math.sin(TAU * ((1 + i % 3) * t / n + i * 0.37)) + 0.2 * math.sin(TAU * (2 * t / n + i * 0.9))
        hs.append(max(0.1, min(1.0, v)))
    lvl = sum(hs[:8]) / 8, sum(hs[8:]) / 8
    unic(f, 1, "OREGON", mix(DOG, OG2, lvl[0]))
    t35c(f, 18, "RADIO CO", mix(DPB, LPB, lvl[1]))
    for i, v in enumerate(hs):
        hh = round(v * 36)
        x = i * 4
        for y in range(63 - hh, 64):
            k = (63 - y) / 36
            d.line((x, y, x + 2, y), fill=_lerp_stops([OG, (200, 220, 40), AMB], k))
        d.line((x, 62 - hh, x + 2, 62 - hh), fill=CREAM)
    return f


# ---------------------------------------------------------------- 10 firs
def _fir_strip(seed, h, masts, col):
    r = random.Random(seed)
    s = Image.new("RGB", (64, 64))
    d = ImageDraw.Draw(s)
    xs = list(range(2, 64, 9 if h > 20 else 6))
    tops = []
    for i, x in enumerate(xs):
        hh = h + r.randrange(-4, 5)
        for dx in (0, 64):
            d.polygon([(x + dx - hh // 3, 63), (x + dx, 63 - hh), (x + dx + hh // 3, 63)], fill=col)
        tops.append((x, 63 - hh, i in masts))
    return s, tops


_F1 = _fir_strip(4, 30, {2, 5}, (20, 120, 50))
_F2 = _fir_strip(9, 16, set(), DOG)


@p64("p_orco_firs", n=32, ms=90, note="two parallax layers of firs drift by, antenna treetops blink + TREETOP FM")
def firs(t, n):
    f = canvas()
    for y in range(40):
        for x in range(64):
            put(f, x, y, mix((0, 6, 22), (10, 30, 60), y / 40))
    for (s, tops), sp, y0 in ((_F2, 1, -8), (_F1, 2, 0)):
        off = 64 * sp * t // n
        win = scroll(s, off, 0, 0, 64)
        f.paste(win, (0, y0), win.convert("L").point(lambda v: 255 if v else 0))
        if sp == 2:
            d = ImageDraw.Draw(f)
            for x, y, m in tops:
                if m:
                    xx = (x - off) % 64
                    d.line((xx, y, xx, y - 6), fill=(150, 160, 170))
                    put(f, xx, y - 7, RED if (t // 4) % 2 == 0 else dim(RED, 0.25))
    t35c(f, 2, "TREETOP", AMB)
    t35c(f, 9, "FM", CREAM)
    return f


# ---------------------------------------------------------------- 11 glitch
def _finder(d, x, y):
    d.rectangle((x, y, x + 13, y + 13), outline=WHITE, width=2)
    d.rectangle((x + 4, y + 4, x + 9, y + 9), fill=WHITE)


_GT = uni_mask("TheOrc")
_GT2 = uni_mask(".dev")


@p64("p_orco_glitch", n=36, ms=90, note="QR finders + module noise glitch-resolve into TheOrc.dev, hold, dissolve")
def glitch(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    _finder(d, 1, 1)
    _finder(d, 49, 1)
    _finder(d, 1, 49)
    k = min(1.0, max(0.0, (t - 4) / 10)) if t < n // 2 else min(1.0, max(0.0, (n - 4 - t) / 10))
    tgt = Image.new("L", (64, 64))
    tgt.paste(_GT, ((64 - _GT.width) // 2, 16))
    tgt.paste(_GT2, ((64 - _GT2.width) // 2 + 6, 32))
    tp = tgt.load()
    r = random.Random(t)
    for y in range(16, 48, 2):
        for x in range(0, 64, 2):
            if r.random() < 0.35 * (1 - k):
                d.rectangle((x, y, x + 1, y + 1), fill=dim(OG2 if r.random() < 0.5 else LPB, 0.7))
    for y in range(64):
        for x in range(64):
            if tp[x, y] and r.random() < k ** 0.7 + 0.02:
                put(f, x, y, OG2 if y < 32 else AMB)
    if 0 < k < 1:
        y = r.randrange(16, 48)
        d.line((0, y, 63, y), fill=dim(PB, 0.6))
    return f


# ---------------------------------------------------------------- 12 logospin
def _coin_face(front: bool):
    s = Image.new("RGB", (30, 30))
    d = ImageDraw.Draw(s)
    d.ellipse((0, 0, 29, 29), fill=DAMB, outline=AMB)
    d.ellipse((2, 2, 27, 27), outline=dim(AMB, 0.6))
    if front:
        uni(s, 3, 7, "ORC", CREAM)
    else:
        fm, ol, _ = oregon(14)
        paint_mask(s, fm, 15 - fm.width // 2, 8, OG)
    return s


_CF = (_coin_face(True), _coin_face(False))


@p64("p_orco_logospin", n=24, ms=90, note="ORC coin spins in 3D (Oregon on the reverse) + TheOrc.dev / OREGON RADIO")
def logospin(t, n):
    f = canvas()
    th = TAU * t / n
    c = math.cos(th)
    w = max(1, round(abs(c) * 30))
    face = _CF[0] if c >= 0 else _CF[1]
    img = face.resize((w, 30), Image.Resampling.NEAREST)
    f.paste(img, (32 - w // 2, 4))
    if w < 4:
        ImageDraw.Draw(f).line((32, 4, 32, 33), fill=AMB)
    t35c(f, 40, "OREGON", OG2)
    t35c(f, 47, "RADIO", OG2)
    t35c(f, 56, "THEORC.DEV", LPB)
    return f


# ---------------------------------------------------------------- 13 marquee
def _marq_strip():
    fm, _, _ = oregon(16)
    txt = uni_mask("OREGON RADIO CO")
    w = txt.width + 8 + fm.width + 8
    w += (-w) % 4                      # 2x strip scrolls 8 px/frame: width must divide evenly
    s = Image.new("RGB", (w, 16))
    paint_mask(s, txt, 0, 0, brand_grad(0, txt.width))
    paint_mask(s, fm, txt.width + 8, 0, OG)
    return s


_MQ = _marq_strip()


@p64("p_orco_marquee", n=None, ms=130, note="seamless 2x OREGON RADIO CO marquee + filled Oregon separator")
def marquee(t, n):
    f = canvas()
    fm, ol, inner = oregon(22)
    paint_mask(f, fm, 32 - fm.width // 2, 2, OG)
    big = _MQ.resize((_MQ.width * 2, 32), Image.Resampling.NEAREST)
    scroll(big, 8 * t, 0, 28, 64, f)
    ImageDraw.Draw(f).line((0, 62, 63, 62), fill=DOG)
    return f


# ---------------------------------------------------------------- 14 mascot
@p64("p_orco_mascot", n=32, ms=90, note="pixel orc with whip antenna sending rings, blinks once per loop, ORC + ON AIR")
def mascot(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    orc(f, 2, 22, 2)
    if t in (12, 13):
        d.rectangle((2 + 8, 22 + 12, 2 + 23, 22 + 15), fill=(60, 150, 40))
    d.line((34, 46, 40, 8), fill=(170, 170, 180))
    put(f, 40, 7, AMB)
    for k in range(3):
        r = 3 + ((t / n) * 3 + k / 3) % 1 * 22
        d.arc((40 - r, 7 - r, 40 + r, 7 + r), -60, 40, fill=dim(AMB, 1 - r / 26))
    unic(f, 48, "ORC", OG2, 1, 30, 63)
    on = ((t + 4) // 8) % 2 == 0
    d.rounded_rectangle((44, 30, 62, 40), radius=2, fill=RED if on else dim(RED, 0.25))
    t35(f, 46, 33, "AIR", WHITE if on else dim(WHITE, 0.4))
    return f


# ---------------------------------------------------------------- 15 morse
_MORSE = [("O", "---"), ("R", ".-."), ("C", "-.-.")]
U = 2


def _tape():
    cells = []
    for li, (_, code) in enumerate(_MORSE):
        for si, s in enumerate(code):
            cells += [li + 1] * (3 if s == "-" else 1)
            cells += [0]
        cells += [0, 0]
    cells += [0] * 4
    return cells


_TAPE = _tape()


@p64("p_orco_morse", n=len(_TAPE), ms=90, note="ORC in Morse: tape scrolls under a cursor, lamp keys, letter lights")
def morse(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    cur = _TAPE[t % n]
    for i, (L, code) in enumerate(_MORSE):
        x = 6 + i * 20
        uni(f, x, 2, L, AMB if cur == i + 1 else dim(OG, 0.5), 1)
        t35(f, x - 2 + (8 - width35(code)) // 2, 20, code, CREAM if cur == i + 1 else dim(CREAM, 0.35))
    for j in range(-16, 17):
        c = _TAPE[(t + j) % n]
        x = 32 + j * U
        d.rectangle((x, 36, x + U - 1, 39), fill=OG2 if c else (20, 30, 20))
    d.line((32, 33, 32, 42), fill=AMB)
    d.ellipse((26, 46, 38, 58), fill=AMB if cur else (40, 25, 0), outline=DAMB)
    t35(f, 2, 50, "KEY", dim(CREAM, 0.6))
    t35(f, 44, 50, "ORC", dim(CREAM, 0.6))
    return f


# ---------------------------------------------------------------- 16 mthood
@p64("p_orco_mthood", n=24, ms=100, note="snow-capped Mt Hood, summit antenna broadcasting rings, Oregon locator + MT HOOD")
def mthood(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.polygon([(0, 56), (24, 22), (32, 18), (40, 22), (63, 56)], fill=(40, 70, 60))
    d.polygon([(24, 22), (32, 18), (40, 22), (36, 28), (33, 25), (29, 29), (26, 26), (20, 28)], fill=WHITE)
    d.rectangle((0, 56, 63, 63), fill=DOG)
    d.line((32, 18, 32, 9), fill=(170, 170, 180))
    put(f, 32, 8, RED if (t // 6) % 2 == 0 else AMB)
    for k in range(2):
        r = 3 + ((t / n) * 2 + k / 2) % 1 * 26
        d.arc((32 - r, 8 - r, 32 + r, 8 + r), 200, 340, fill=dim(AMB, 1 - r / 30))
    fm, ol, _ = oregon(12)
    paint_mask(f, ol, 63 - fm.width, 0, OG)
    x, y = city("Mt Hood", 12, 63 - fm.width, 0)
    put(f, x, y, AMB)
    t35(f, 2, 2, "MT", CREAM)
    t35(f, 2, 9, "HOOD", CREAM)
    t35c(f, 58, "11249 FT", dim(CREAM, 0.7))
    return f


# ---------------------------------------------------------------- 17 neon
_FLICK = [1, 1, 1, 0.3, 1, 1, 1, 1, 0.5, 0.2, 1, 1, 1, 1, 1, 1, 0.4, 1, 1, 1, 1, 1, 1, 1, 0.6, 1, 1, 1, 1, 1, 1, 1]


@p64("p_orco_neon", n=32, ms=90, note="neon shop sign: OPEN tube with a stuttering glow, buzzing ON AIR, amber border")
def neon(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rounded_rectangle((0, 0, 63, 63), radius=8, outline=dim(AMB, 0.55 + 0.45 * (t % 2)))
    d.rounded_rectangle((2, 2, 61, 61), radius=7, outline=dim(DAMB, 0.8))
    k = _FLICK[t % 32]
    m = uni_mask("OPEN", 2) if uw("OPEN", 2) <= 60 else uni_mask("OPEN")
    glow = m.resize(m.size).filter(__import__("PIL.ImageFilter", fromlist=["x"]).MaxFilter(3))
    x = 32 - m.width // 2
    paint_mask(f, glow, x, 6, dim(DPB, k))
    paint_mask(f, m, x, 6, dim(LPB, 0.25 + 0.75 * k))
    buzz = 0.75 + 0.25 * math.sin(TAU * 4 * t / n)
    d.rounded_rectangle((8, 42, 55, 55), radius=3, outline=dim(RED, buzz))
    uni(f, 32 - uw("ON AIR") // 2, 41, "ON AIR", dim(RED, buzz))
    return f


# ---------------------------------------------------------------- 18 night
_ST = stars(21, 26, 64, 40)
_NS = stars(33, 10, 64, 40)


@p64("p_orco_night", n=16, ms=120, note="twinkling + drifting stars, moon, tower with aviation light + ORC RADIO / LATE NITE")
def night(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    twinkle(f, _ST, t, n)
    for x, y, _ in _NS:
        put(f, (x - 4 * t) % 64, y, WHITE)
    d.ellipse((46, 4, 57, 15), fill=CREAM)
    d.ellipse((50, 2, 61, 13), fill=BLACK)
    tower(f, 14, 56, 22, lamp=RED if (t // 2) % 2 == 0 else dim(RED, 0.2))
    for k in range(2):
        r = 3 + ((t / n) * 2 + k / 2) % 1 * 20
        d.arc((14 - r, 33 - r, 14 + r, 33 + r), -70, 70, fill=dim(AMB, 1 - r / 24))
    d.rectangle((0, 57, 63, 63), fill=BODY)
    t35(f, 28, 42, "ORC", OG2)
    t35(f, 28, 49, "RADIO", OG2)
    t35c(f, 58, "LATE NITE", AMB, 0, 63)
    return f


# ---------------------------------------------------------------- 19 printer
_PM = uni_mask("ORC", 2) if uw("ORC", 2) <= 60 else uni_mask("ORC")


@p64("p_orco_printer", n=40, ms=90, note="3D-printer nozzle lays ORC layer by layer (hot amber cools to green), % + DONE")
def printer(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    rows = _PM.height
    build = n - 12
    if t < build:
        lay = round(rows * t / build)
    elif t < n - 4:
        lay = rows
    else:
        lay = rows
    fade = 1.0 if t < n - 4 else (n - t) / 5
    x0, y0 = 32 - _PM.width // 2, 50 - rows
    mp = _PM.load()
    px = f.load()
    for y in range(rows):
        if y >= rows - lay:
            age = lay - (rows - y)
            c = mix(OG, AMB, max(0.0, 1 - age / 4)) if t < build else OG
            for x in range(_PM.width):
                if mp[x, y]:
                    px[x0 + x, y0 + y] = dim(c, fade)
    d.rectangle((0, 50, 63, 52), fill=(60, 60, 70))
    if t < build:
        ny = y0 + rows - lay - 3
        nx = x0 + round((_PM.width - 1) * tri(t / 4))
        d.polygon([(nx - 3, ny - 4), (nx + 3, ny - 4), (nx, ny)], fill=(170, 170, 180))
        put(f, nx, ny + 1, AMB)
        t35c(f, 57, f"{round(100 * t / build)}%", CREAM)
    else:
        t35c(f, 57, "DONE", OG2 if fade > 0.5 else dim(OG2, fade))
    return f


# ---------------------------------------------------------------- 20 radar
_RC = ["Portland", "Eugene", "Bend", "Pendleton"]


@p64("p_orco_radar", n=24, ms=100, note="radar sweep inside the Oregon outline, city blips flare as it passes + bearing")
def radar(t, n):
    f = canvas()
    ox, oy, h = 4, 6, 40
    fm, ol, inner = oregon(h)
    paint_mask(f, inner, ox, oy, (0, 18, 6))
    cx, cy = ox + fm.width // 2, oy + h // 2
    a = TAU * t / n
    px = f.load()
    mp = fm.load()
    for y in range(h):
        for x in range(fm.width):
            if mp[x, y]:
                ang = math.atan2(oy + y - cy, ox + x - cx) % TAU
                dd = (a - ang) % TAU
                if dd < 1.2:
                    px[ox + x, oy + y] = mix((0, 18, 6), OG, (1 - dd / 1.2) * 0.8)
    paint_mask(f, ol, ox, oy, OG2)
    for c in _RC:
        x, y = city(c, h, ox, oy)
        ang = math.atan2(y - cy, x - cx) % TAU
        dd = (a - ang) % TAU
        k = max(0.0, 1 - dd / 2.5)
        ImageDraw.Draw(f).rectangle((x - 1, y - 1, x, y), fill=mix(DAMB, AMB, k))
    brg = round((math.degrees(a) + 90) % 360)
    t35c(f, 52, f"BRG {brg:03d}", CREAM)
    t35c(f, 58, "ORC RADAR", OG2)
    return f


# ---------------------------------------------------------------- 21 rain
_DROPS = [(random.Random(i).randrange(64), random.Random(i * 7).randrange(64), 1 + i % 2) for i in range(60)]


@p64("p_orco_rain", n=16, ms=90, note="rain over Oregon, amber rings from Portland punch through + RAIN OR / SHINE")
def rain(t, n):
    f = canvas()
    ox, oy, h = 4, 2, 40
    state(f, ox, oy, h, fill=(4, 22, 12))
    d = ImageDraw.Draw(f)
    x, y = city("Portland", h, ox, oy)
    for k in range(2):
        r = 2 + ((t / n) + k / 2) % 1 * 30
        ring(d, x, y, r, dim(AMB, 1 - r / 34))
    put(f, x, y, CREAM)
    for dx, dy, sp in _DROPS:
        yy = (dy + 64 * sp * t // n) % 64
        xx = (dx - yy // 8) % 64
        put(f, xx, yy, dim(LPB, 0.5 + 0.2 * sp))
        put(f, xx, (yy - 1) % 64, dim(LPB, 0.25))
    t35c(f, 46, "RAIN OR", LPB)
    t35c(f, 54, "SHINE", AMB)
    return f


# ---------------------------------------------------------------- 22 satellite
@p64("p_orco_satellite", n=40, ms=90, note="satellite arcs over Oregon, downlink to Bend; LOS/ACQ/TRK + ORC SAT")
def satellite(t, n):
    f = canvas()
    ox, oy, h = 6, 24, 30
    state(f, ox, oy, h)
    d = ImageDraw.Draw(f)
    gx, gy = city("Bend", h, ox, oy)
    d.rectangle((gx - 1, gy - 1, gx + 1, gy + 1), fill=AMB)
    pass_n = n - 8
    if t < pass_n:
        u = t / (pass_n - 1)
        sx = -4 + 72 * u
        sy = 22 - 18 * math.sin(math.pi * u)
        st = "ACQ" if u < 0.15 or u > 0.85 else "TRK"
        d.rectangle((sx - 1, sy, sx + 1, sy + 1), fill=WHITE)
        d.line((sx - 4, sy, sx - 2, sy), fill=LPB)
        d.line((sx + 2, sy, sx + 4, sy), fill=LPB)
        if st == "TRK":
            for k in range(0, 40, 4):
                v = ((k / 40) + t / 4) % 1
                put(f, round(sx + (gx - sx) * v), round(sy + (gy - sy) * v), dim(AMB, 0.8))
    else:
        st = "LOS"
    for x in range(0, 64, 4):
        put(f, x, round(22 - 18 * math.sin(math.pi * (x + 4) / 72)), dim(LPB, 0.25))
    t35(f, 2, 1, "ORC SAT", OG2)
    t35(f, 50, 1, st, {"LOS": RED, "ACQ": AMB, "TRK": OG2}[st])
    t35c(f, 58, "BEND GS", dim(CREAM, 0.8))
    return f


# ---------------------------------------------------------------- 23 scope
@p64("p_orco_scope", n=32, ms=90, note="phosphor 3:2 Lissajous turning on a graticule, bright head + ORC LAB / X:Y 3:2")
def scope(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    for g in range(4, 48, 8):
        for k in range(4, 61, 2):
            put(f, k, g + 2, (0, 30, 10))
    for g in range(4, 61, 8):
        for k in range(6, 48, 2):
            put(f, g, k, (0, 30, 10))
    d.rectangle((2, 4, 61, 48), outline=DOG)
    ph = TAU * t / n
    pts = [(32 + 26 * math.sin(3 * s + ph), 26 + 19 * math.sin(2 * s)) for s in [TAU * i / 240 for i in range(240)]]
    for x, y in pts:
        put(f, round(x), round(y), (40, 170, 70))
    head = int(240 * ((t / n) * 2 % 1))
    for j in range(8):
        x, y = pts[(head - j) % 240]
        put(f, round(x), round(y), mix(OG2, WHITE, 1 - j / 8))
    t35(f, 2, 52, "ORC LAB", OG2)
    t35(f, 2, 58, "X:Y 3:2", AMB)
    return f


# ---------------------------------------------------------------- 24 sunset
@p64("p_orco_sunset", n=16, ms=120, note="sun on the Pacific, shimmering glint path, AM wave rolling across the sky + ORC AM")
def sunset(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    for y in range(40):
        for x in range(64):
            put(f, x, y, _lerp_stops([(20, 0, 40), (120, 30, 60), (255, 110, 30)], y / 40))
    d.ellipse((20, 26, 44, 50), fill=(255, 190, 60))
    for y in range(40, 64):
        for x in range(64):
            put(f, x, y, mix((0, 20, 60), (0, 40, 100), (y - 40) / 24))
    for y in range(41, 64, 2):
        w = 10 - (y - 40) // 3 + 3
        for x in range(32 - w, 32 + w):
            if math.sin(x * 1.3 + y * 2 + TAU * t / n * 2) > 0.3:
                put(f, x, y, AMB)
    ph = TAU * t / n
    for x in range(64):
        env = 1 + 0.6 * math.sin(TAU * x / 32 - ph)
        y = 16 + 5 * env * math.sin(TAU * x / 6 - 4 * ph) * 0.8
        put(f, x, round(y), CREAM)
    t35(f, 2, 2, "ORC AM", WHITE)
    return f


# ---------------------------------------------------------------- 25 terminal
_CMD = "THEORC.DEV"
_ANS = ["HELLO", "FROM ORC"]


@p64("p_orco_terminal", n=40, ms=100, note="prompt types TheOrc.dev, server answers HELLO FROM ORC, blinking cursor")
def terminal(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rectangle((0, 0, 63, 6), fill=DOG)
    t35(f, 2, 1, "ORC SH", OG2)
    L = len(_CMD)
    if t < 30:
        k = min(L, t)
    else:
        k = max(0, L - (t - 30))
    t35(f, 2, 12, ">", AMB)
    shown = _CMD[:k]
    t35(f, 7, 12, shown, CREAM)
    cx = 7 + width35(shown) + 1 if shown else 7
    if t < L or t >= 30 or (t // 3) % 2 == 0:
        d.rectangle((cx, 12, cx + 2, 16), fill=OG2)
    if L + 2 <= t < 30:
        uni(f, 2, 22, _ANS[0], OG2)
        if t >= L + 5:
            uni(f, 2, 38, "FROM", OG2)
            uni(f, 38, 38, "ORC", AMB)
    return f


# ---------------------------------------------------------------- 26 tower
@p64("p_orco_tower", n=16, ms=100, note="central-Oregon tower pulsing amber rings across the state + OREGON / RADIO CO")
def tower_(t, n):
    f = canvas()
    ox, oy, h = 4, 2, 40
    state(f, ox, oy, h)
    x, y = city("Bend", h, ox, oy)
    d = ImageDraw.Draw(f)
    for k in range(3):
        r = 2 + ((t / n) + k / 3) % 1 * 30
        ring(d, x, y - 8, r, dim(AMB, 1 - r / 32))
    tower(f, x, y, 9, lamp=RED if t < n // 2 else AMB)
    t35c(f, 47, "OREGON", OG2)
    t35c(f, 55, "RADIO CO", AMB)
    return f


# ---------------------------------------------------------------- 27 tracer
_TR = resample(border_pts(36), 68)


@p64("p_orco_tracer", n=34, ms=90, note="comet traces the real Oregon border + OREGON / RADIO CO / LLC")
def tracer(t, n):
    f = canvas()
    ox, oy = 7, 2
    fm, ol, inner = oregon(36)
    paint_mask(f, ol, ox, oy, DOG)
    m = len(_TR)
    head = 2 * t
    for j in range(14):
        x, y = _TR[(head - j) % m]
        put(f, round(ox + x), round(oy + y), mix(OG2, WHITE, max(0.0, 1 - j / 4)) if j < 4 else dim(OG, 1 - j / 14))
    t35c(f, 42, "OREGON", OG2)
    t35c(f, 49, "RADIO CO", LPB)
    t35c(f, 57, "LLC", AMB)
    return f


# ---------------------------------------------------------------- 28 tube
@p64("p_orco_tube", n=32, ms=100, note="vacuum tube, breathing filament, warm ORC and a magic-eye opening and closing")
def tube(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    br = 0.6 + 0.4 * math.sin(TAU * t / n)
    d.rounded_rectangle((8, 4, 30, 50), radius=10, outline=(120, 130, 140))
    d.rectangle((10, 50, 28, 55), fill=(60, 50, 40))
    for x in (12, 17, 21, 26):
        d.line((x, 55, x, 60), fill=(150, 150, 150))
    d.rectangle((13, 16, 25, 40), outline=(90, 90, 100))
    d.line((19, 18, 19, 38), fill=mix(DAMB, (255, 140, 40), br))
    d.line((18, 22, 20, 22), fill=dim(AMB, br))
    d.line((18, 34, 20, 34), fill=dim(AMB, br))
    for r in range(6, 2, -1):
        d.ellipse((19 - r, 28 - r * 2, 19 + r, 28 + r * 2), outline=dim(AMB, br * 0.25 / (r - 2)))
    uni(f, 34, 4, "ORC", mix(DAMB, AMB, br))
    d.ellipse((38, 30, 58, 50), fill=(0, 40, 20), outline=(90, 90, 100))
    gap = 10 + 60 * tri(t / n)
    d.pieslice((40, 32, 56, 48), -90 + gap / 2, 270 - gap / 2, fill=(60, 240, 120))
    d.ellipse((45, 37, 51, 43), fill=(10, 10, 10))
    return f


# ---------------------------------------------------------------- 29 waterfall
_WF = waterfall(64, 40, 16, seed=7, q=6)


@p64("p_orco_waterfall", n=16, ms=120, note="Oregon shape filled with a scrolling SDR waterfall, spectrum trace + ORC SDR")
def waterfall_(t, n):
    f = canvas()
    ox, oy, h = 4, 16, 40
    fm, ol, inner = oregon(h)
    wf = _WF(t).crop((0, 0, fm.width, h))
    f.paste(wf, (ox, oy), fm)
    paint_mask(f, ol, ox, oy, OG2)
    lv = lv_anim(t, n, 64)
    spectrum(f, 0, 0, 64, 12, lv, every=1)
    t35c(f, 58, "ORC SDR", AMB)
    return f


# ---------------------------------------------------------------- 30 wireframe
_WP = resample(border_pts(28), 48)


@p64("p_orco_wireframe", n=24, ms=90, note="real Oregon outline as an extruded 3D wireframe spinning + OREGON / TheOrc.dev")
def wireframe(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    cx0 = sum(p[0] for p in _WP) / len(_WP)
    cy0 = sum(p[1] for p in _WP) / len(_WP)
    a = TAU * t / n
    ca, sa = math.cos(a), math.sin(a)

    def P(x, y, z):
        X, Z = (x - cx0) * ca - z * sa, (x - cx0) * sa + z * ca
        s = 60 / (60 + Z)
        return 32 + X * s, 24 + (y - cy0) * s

    front = [P(x, y, -4) for x, y in _WP]
    back = [P(x, y, 4) for x, y in _WP]
    d.line(back + [back[0]], fill=DOG)
    for i in range(0, len(_WP), 6):
        d.line((front[i], back[i]), fill=dim(OG, 0.6))
    d.line(front + [front[0]], fill=OG2)
    t35c(f, 48, "OREGON", OG2)
    t35c(f, 56, "THEORC.DEV", LPB)
    return f


def setup():
    from p64lib import REGISTRY
    REGISTRY["p_orco_marquee"]["n"] = _MQ.width * 2 // 8
