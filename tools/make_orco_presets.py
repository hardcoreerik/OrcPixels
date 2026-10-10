"""15 'Oregon Radio Company' presets (family orc_co, p_orco_<name>).

ORC = Oregon Radio Company: a brand pun on OrcSDR / TheOrc. Seven of them use
the real Oregon outline from tools/oregon_shape.py (Natural Earth 1:10m,
public domain), saved as assets/oregon_*.png.

    python tools/make_orco_presets.py                 # build all 15 + contact sheet/preview
    python tools/make_orco_presets.py p_orco_radar    # build selected (no sheet)
    python tools/make_orco_presets.py --sheet OUT.png --preview OUT.gif

Seamless loops: every motion is a function of the phase t/N with integer cycles,
scrolls wrap by exactly their strip width, and holds start/end on matching
frames (checked by orcpixels.loopmetric / tools/loopcheck.py). Art only: no
preset claims live data.
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import oregon_shape as OR  # noqa: E402
from make_styles import LoopWaterfall  # noqa: E402
from orcpixels.loopmetric import loop_metric_frames  # noqa: E402
from orcpixels.render import _lerp_stops, encode_gif  # noqa: E402
from pixfont import text35, width35  # noqa: E402

OUT = ROOT / "presets"
W, H = 64, 16
FAMILY = "orc_co"
TAU = 2 * math.pi
BLACK = (0, 0, 0)
# palette: Oregon green, Pacific blue, amber (+ cream highlight)
OG, OG2, DOG, BODY = (40, 210, 90), (140, 245, 160), (12, 80, 34), (5, 28, 12)
PB, LPB, DPB = (30, 140, 255), (120, 200, 255), (0, 38, 90)
AMB, DAMB, CREAM, RED = (255, 168, 20), (120, 64, 0), (255, 236, 190), (255, 50, 40)
UNI = ImageFont.truetype(str(ROOT / "orcpixels/fonts/unifont.otf"), 16)
OUTLINE_PRESETS = set()

REGISTRY: list[dict] = []


_BATCH = 1


def preset(name, title, desc, n, ms=100, outline=False, colors=16, domain=False, key=None):
    def deco(fn):
        REGISTRY.append(dict(name=name, family=FAMILY, title=title, desc=desc, n=n, ms=ms, fn=fn, outline=outline,
                             colors=colors, domain=domain, batch=_BATCH, key=key))
        if outline:
            OUTLINE_PRESETS.add(name)
        return fn
    return deco


# ------------------------------------------------------------------ helpers
def canvas(bg=BLACK):
    return Image.new("RGB", (W, H), bg)


def dim(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def mix(a, b, k):
    k = max(0.0, min(1.0, k))
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def grad(x0, w, stops):
    return lambda gx, gy: _lerp_stops(stops, (gx - x0) / max(1, w - 1))


def put(img, x, y, c):
    x, y = int(round(x)), int(round(y))
    if 0 <= x < img.width and 0 <= y < img.height:
        img.putpixel((x, y), c)


def place(m: Image.Image, x: int, y: int) -> Image.Image:
    """Small L mask -> full 64x16 L mask at (x, y)."""
    full = Image.new("L", (W, H), 0)
    full.paste(m, (x, y))
    return full


def paint(f, m, color):
    """Fill the 255 pixels of a full-size L mask with color (tuple or fn(x, y))."""
    if callable(color):
        px, mp = f.load(), m.load()
        for y in range(H):
            for x in range(W):
                if mp[x, y]:
                    px[x, y] = color(x, y)
    else:
        f.paste(Image.new("RGB", (W, H), color), (0, 0), m)


def uni_mask(s: str) -> Image.Image:
    w = max(1, int(UNI.getlength(s)))
    m = Image.new("L", (w, 16))
    ImageDraw.Draw(m).text((0, 0), s, fill=255, font=UNI)
    return m.point(lambda p: 255 if p > 128 else 0)


def uni(f, x, y, s, color):
    m = Image.new("L", (W, H))
    m.paste(uni_mask(s), (x, y))
    paint(f, m, color)


def arc(d, cx, cy, r, a0, a1, color):
    d.arc((cx - r, cy - r, cx + r, cy + r), a0, a1, fill=color)


def state(h=16):
    """(fill, outline, interior) L masks of Oregon at height h (small, not placed)."""
    fm, ol = OR.mask(h), OR.outline(h)
    inner = Image.new("L", fm.size)
    inner.paste(fm, (0, 0), ol.point(lambda v: 255 - v))
    return fm, ol, inner


def city(name, h=16, x0=0, y0=0):
    x, y = OR.project(*OR.CITIES[name], h)
    return x + x0, y + y0


def tower(f, x, y, h=5, color=(150, 160, 170), lamp=None):
    """Lattice mast with base at (x, y), top at y-h."""
    d = ImageDraw.Draw(f)
    d.line((x, y, x, y - h), fill=color)
    d.point([(x - 1, y), (x + 1, y)], fill=color)
    if h >= 4:
        d.point([(x - 1, y - 2), (x + 1, y - 2)], fill=dim(color, 0.6))
    if lamp:
        put(f, x, y - h - 1, lamp)


def two_lines(f, x, l1, c1, l2, c2, y1=2, y2=9):
    text35(f, x, y1, l1, c1)
    text35(f, x, y2, l2, c2)


# ===================================================================== 15 presets
@preset("p_orco_tower", "Broadcast tower", "Oregon outline with a central-Oregon tower pulsing amber radio rings across the state + 'OREGON / RADIO CO'",
        n=16, ms=90, outline=True)
def _(t, n):
    f = canvas()
    fm, ol, inner = state(16)
    tx, ty = 10, 9
    layer = canvas(BODY); d = ImageDraw.Draw(layer)
    for k in range(3):
        r = ((t / n + k / 3) % 1.0) * 15 + 1.5
        arc(d, tx, ty - 3, r, 0, 360, dim(AMB, max(0.15, 1 - r / 16)))
    f.paste(layer, (0, 0), place(inner, 0, 0))
    paint(f, place(ol, 0, 0), OG)
    tower(f, tx, ty + 1, 4, CREAM, lamp=RED if t % 8 < 4 else DAMB)
    two_lines(f, 25, "OREGON", grad(25, 23, [OG2, OG]), "RADIO CO", grad(25, 31, [LPB, PB]))
    return f


@preset("p_orco_radar", "Radar sweep", "Radar beam sweeping inside the Oregon outline, Portland/Eugene/Bend/Pendleton blips flare as it passes + bearing readout",
        n=32, ms=80, outline=True)
def _(t, n):
    f = canvas()
    fm, ol, inner = state(16)
    cx, cy = 10.5, 8.0
    a = TAU * t / n
    px = f.load(); ip = inner.load()
    for y in range(16):
        for x in range(fm.width):
            if not ip[x, y]:
                continue
            th = math.atan2(y - cy, x - cx)
            behind = (a - th) % TAU
            k = max(0.0, 1 - behind / (TAU * 0.3))
            k = round(k * 3) / 3
            px[x, y] = mix(BODY, OG, k) if k < 1 else OG2
    for c in ("Portland", "Eugene", "Bend", "Pendleton"):
        x, y = city(c)
        behind = (a - math.atan2(y - cy, x - cx)) % TAU
        k = max(0.0, 1 - behind / (TAU * 0.5))
        put(f, x, y, mix(DAMB, CREAM, k))
    paint(f, place(ol, 0, 0), dim(OG, 0.8))
    text35(f, 25, 0, "ORC RADAR", AMB)
    text35(f, 25, 6, "OREGON", OG)
    brg = int(round((math.degrees(a) + 90) % 360)) % 360
    text35(f, 25, 11, f"BRG {brg:03d}", LPB)
    return f


_WF = LoopWaterfall(22, 16, 32, seed=11, carriers=((0.25, 0.9, 0.05, 1), (0.6, 0.6, 0.08, 2), (0.85, 0.4, 0.03, 1)))
WF_STOPS = [BLACK, DPB, PB, OG, AMB, CREAM]


@preset("p_orco_waterfall", "Oregon waterfall", "The Oregon shape filled with a scrolling SDR waterfall, its live-style spectrum trace beside it + 'ORC SDR'",
        n=32, ms=90, outline=True)
def _(t, n):
    f = canvas()
    fm, ol, inner = state(16)
    wf = _WF.image(t, down=True, stops=WF_STOPS, q=6)
    f.paste(wf, (0, 0), fm)
    paint(f, place(ol, 0, 0), dim(OG2, 0.9))
    text35(f, 25, 0, "ORC SDR", OG)
    top = _WF.rows_at(t, True)[0]
    d = ImageDraw.Draw(f)
    pts = []
    for x in range(24, 64):
        u = (x - 24) * 21 / 39
        i = int(u); fr = u - i
        v = top[i] * (1 - fr) + top[min(21, i + 1)] * fr
        pts.append((x, 15 - round(v * 8)))
    for x, y in pts:
        d.line((x, y + 1, x, 15), fill=DPB)
    d.line(pts, fill=AMB)
    return f


def _marquee_strip():
    icon = OR.mask(12)
    gap = 7
    txt = uni_mask("OREGON RADIO CO")
    w = icon.width + gap + txt.width + 9
    w += (-w) % 3                                  # scroll 3 px/frame: strip width is a multiple of 3
    m_icon = Image.new("L", (w, 16)); m_icon.paste(icon, (0, 2))
    m_txt = Image.new("L", (w, 16)); m_txt.paste(txt, (icon.width + gap, 0))
    strip = Image.new("RGB", (w, 16))
    strip.paste(Image.new("RGB", (w, 16), OG), (0, 0), m_icon)
    x0, tw = icon.width + gap, txt.width
    col = Image.new("RGB", (w, 16)); cd = ImageDraw.Draw(col)
    wr = int(UNI.getlength("OREGON ")); wc = int(UNI.getlength("OREGON RADIO "))
    cd.rectangle((0, 0, x0 + wr - 1, 15), fill=OG2)
    cd.rectangle((x0 + wr, 0, x0 + wc - 1, 15), fill=LPB)
    cd.rectangle((x0 + wc, 0, w, 15), fill=AMB)
    strip.paste(col, (0, 0), m_txt)
    return strip


_MQ = _marquee_strip()  # noqa: E305


@preset("p_orco_marquee", "OREGON RADIO CO marquee", "Seamless unifont marquee 'OREGON RADIO CO' in green-blue-amber, with a filled Oregon state as the separator",
        n=_MQ.width // 3, ms=75, outline=True)
def _(t, n):
    f = canvas()
    off = (3 * t) % _MQ.width
    tiled = Image.new("RGB", (_MQ.width * 2, 16))
    tiled.paste(_MQ, (0, 0)); tiled.paste(_MQ, (_MQ.width, 0))
    f.paste(tiled.crop((off, 0, off + W, 16)), (0, 0))
    return f


ACR = [("OREGON", OG), ("RADIO", PB), ("COMPANY", AMB)]
_ACR_TYPE = sum(len(w) for w, _ in ACR)          # 18 chars typed one per frame
_ACR_N = 8 + _ACR_TYPE + 12 + 7


@preset("p_orco_acronym", "ORC = Oregon Radio Company", "Big 'ORC' whose letters type out OREGON / RADIO / COMPANY beside them with a cursor, hold, then erase",
        n=_ACR_N, ms=100)
def _(t, n):
    f = canvas()
    if t < 8:
        shown = [0, 0, 0]; active = -1
    elif t < 8 + _ACR_TYPE:
        k = t - 8 + 1; shown = []; active = -1
        for i, (w, _) in enumerate(ACR):
            s = max(0, min(len(w), k)); shown.append(s)
            if 0 < k <= len(w) and active < 0:
                active = i
            k -= len(w)
    elif t < 8 + _ACR_TYPE + 12:
        shown = [len(w) for w, _ in ACR]; active = -1
    else:
        e = t - (8 + _ACR_TYPE + 12) + 1          # erase 1..7: all lines shrink together
        shown = [max(0, len(w) - e) for w, _ in ACR]; active = -1
    for i, (w, c) in enumerate(ACR):
        uni(f, i * 8, 0, w[0], CREAM if i == active else c)
        y = (0, 6, 11)[i]
        if shown[i]:
            text35(f, 28, y, w[:shown[i]], dim(c, 0.8))
            text35(f, 28, y, w[0], c)
        if i == active and shown[i] < len(w):
            ImageDraw.Draw(f).rectangle((28 + shown[i] * 4, y, 30 + shown[i] * 4, y + 4), fill=CREAM)
    return f


def _fx(fq):
    return 4 + (fq - 88) * 2.8


@preset("p_orco_dial", "Frequency dial", "FM dial 88-108 with an amber needle sweeping; 'ORC FM' lights and the signal bars rise when it crosses 97.1",
        n=32, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    for lab in (88, 94, 100, 106):
        s = str(lab)
        text35(f, int(round(_fx(lab) - width35(s) / 2)) if lab > 88 else 2, 0, s, dim(CREAM, 0.7))
    for fq in range(88, 109, 2):
        x = round(_fx(fq))
        major = (fq - 88) % 6 == 0
        d.line((x, 6, x, 7 if major else 6), fill=AMB if major else DAMB)
    d.line((round(_fx(97.1)), 8, round(_fx(97.1)), 8), fill=OG)
    fq = 88 + 20 * (0.5 - 0.5 * math.cos(TAU * t / n))
    lock = max(0.0, 1 - abs(fq - 97.1) / 1.6)
    text35(f, 1, 11, f"{fq:5.1f}", LPB)
    text35(f, 24, 11, "ORC FM", mix(dim(OG, 0.5), OG2, lock) if lock < 0.95 else CREAM)
    for i in range(5):
        on = lock * 5 > i + 0.2
        d.line((52 + i * 2, 15, 52 + i * 2, 15 - i - 1), fill=(AMB if on else (30, 22, 8)))
    x = round(_fx(fq))
    d.line((x, 5, x, 9), fill=RED)
    put(f, x, 4, AMB)
    return f


@preset("p_orco_tube", "Tube radio glow", "Vintage vacuum tube with a breathing filament, warm 'ORC' and a magic-eye tuning indicator opening and closing",
        n=32, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    b = 0.78 + 0.18 * math.sin(TAU * t / n) + 0.04 * math.sin(TAU * 3 * t / n)
    glass = (60, 75, 95)
    d.rounded_rectangle((1, 1, 10, 13), radius=3, outline=glass)
    d.line((3, 14, 8, 14), fill=(110, 110, 110)); d.point([(3, 15), (5, 15), (6, 15), (8, 15)], fill=(150, 150, 150))
    d.rectangle((3, 4, 8, 10), outline=(85, 85, 90))
    glow = mix(DAMB, AMB, b)
    d.rectangle((4, 5, 7, 9), fill=dim(glow, 0.45))
    d.line((5, 6, 5, 9), fill=mix(AMB, CREAM, b - 0.3)); d.line((6, 6, 6, 9), fill=mix(AMB, CREAM, b - 0.3))
    uni(f, 14, 0, "ORC", lambda x, y: mix(DAMB, mix(AMB, CREAM, (b - 0.6) * 1.5), 0.55 + 0.45 * b) if y > 4 else mix(DAMB, AMB, b))
    cx, cy, r = 52, 8, 6
    open_ = 25 + 55 * (0.5 + 0.5 * math.sin(TAU * 2 * t / n + 1.0))
    d.ellipse((cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1), outline=(40, 50, 45))
    d.pieslice((cx - r, cy - r, cx + r, cy + r), -90 + open_ / 2, 270 - open_ / 2, fill=mix(DOG, OG2, b))
    d.ellipse((cx - 1, cy - 1, cx + 1, cy + 1), fill=(20, 20, 20))
    return f


@preset("p_orco_mthood", "Mt Hood broadcast", "Snow-capped Mt Hood with an antenna on the summit broadcasting rings, small Oregon outline marking the spot + 'MT HOOD'",
        n=24, ms=90, outline=True)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    px, py = 17, 4
    for y in range(py, 16):
        half = (y - py) * 1.25
        for x in range(int(px - half), int(px + half) + 1):
            if 0 <= x < 64:
                snow = y <= py + 3 + (1 if (x * 7) % 3 == 0 else 0)
                shade = x > px
                f.putpixel((x, y), (CREAM if not shade else (170, 190, 210)) if snow else (DOG if not shade else (6, 50, 22)))
    ring_layer = canvas(); rd = ImageDraw.Draw(ring_layer)
    for k in range(3):
        r = ((t / n + k / 3) % 1.0) * 13 + 2
        arc(rd, px, py - 3, r, 190, 350, dim(AMB, max(0.2, 1 - r / 15)))
    sky = Image.new("L", (W, H), 0)
    for y in range(16):
        for x in range(34):
            if f.getpixel((x, y)) == BLACK:
                sky.putpixel((x, y), 255)
    f.paste(ring_layer, (0, 0), sky)
    d.line((px, py, px, py - 3), fill=(180, 180, 190))
    put(f, px, py - 4, RED if t % 6 < 3 else DAMB)
    fm, ol, _ = state(9)
    ox, oy = 39, 0
    paint(f, place(fm, ox, oy), BODY)
    paint(f, place(ol, ox, oy), OG)
    hx, hy = city("Mt Hood", 9, ox, oy)
    put(f, hx, hy, AMB if t % 6 < 3 else CREAM)
    text35(f, 36, 11, "MT HOOD", LPB)
    return f


CITY_PLAN = [("Portland", "PORTLAND", "FM 91.5"), ("Eugene", "EUGENE", "FM 89.3"), ("Bend", "BEND", "FM 97.1")]


@preset("p_orco_cities", "ORC network map", "Oregon outline with Portland, Eugene and Bend stations taking turns on air: ring pulse, packet to the next city, name + frequency",
        n=30, ms=100, outline=True)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    fm, ol, inner = state(16)
    paint(f, place(inner, 0, 0), BODY)
    pts = [city(c) for c, _, _ in CITY_PLAN]
    for i in range(3):
        d.line((*pts[i], *pts[(i + 1) % 3]), fill=DPB)
    paint(f, place(ol, 0, 0), OG)
    seg = n // 3
    tt = (t + seg // 2) % n                      # station switches mid-loop, not at the seam
    i, u = tt // seg, (tt % seg) / seg
    a, b = pts[i], pts[(i + 1) % 3]
    if u > 0.45:
        v = (u - 0.45) / 0.55
        put(f, a[0] + (b[0] - a[0]) * v, a[1] + (b[1] - a[1]) * v, LPB)
    r = 1 + u * 5
    arc(d, a[0], a[1], r, 0, 360, dim(AMB, 1 - u * 0.8))
    for j, p in enumerate(pts):
        put(f, *p, CREAM if j == i else AMB)
    _, label, fqs = CITY_PLAN[i]
    text35(f, 25, 0, "ORC NET", AMB)
    text35(f, 25, 6, label, OG2)
    text35(f, 25, 11, fqs, LPB)
    return f


@preset("p_orco_badge", "Est. badge", "Bordered 'EST. 2026' badge: big amber 'ORC', Oregon outline seal, a glint sweeping across once per loop",
        n=32, ms=80, outline=True)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.rectangle((0, 0, 63, 15), outline=DAMB)
    for x, y in ((1, 1), (62, 1), (1, 14), (62, 14)):
        put(f, x, y, AMB)
    text35(f, 3, 3, "EST.", OG2)
    text35(f, 3, 9, "2026", OG2)
    uni(f, 20, 0, "ORC", grad(20, 24, [AMB, CREAM, AMB]))
    fm, ol, inner = state(12)
    paint(f, place(inner, 46, 2), DPB)
    paint(f, place(ol, 46, 2), OG)
    hx = -12 + 96 * t / n
    px = f.load()
    for y in range(H):
        for x in range(W):
            if px[x, y] != BLACK and abs(x - hx - (y - 8) * 0.5) < 1.6:
                px[x, y] = mix(px[x, y], (255, 255, 255), 0.75)
    return f


_STARS = random.Random(1859)
STARS_FAR = [(_STARS.randrange(64), _STARS.randrange(9), _STARS.random()) for _ in range(16)]
STARS_NEAR = [(_STARS.randrange(64), _STARS.randrange(10)) for _ in range(6)]


@preset("p_orco_night", "Night broadcast", "Starfield night: twinkling stars, drifting near stars, a moon, tower with blinking aviation light sending rings + 'ORC RADIO / LATE NITE'",
        n=32, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    clear = lambda x, y: not (16 <= x <= 54 and (2 <= y <= 8 or 9 <= y <= 15))   # keep stars off the text
    for x, y, ph in STARS_FAR:
        k = 0.5 + 0.5 * math.sin(TAU * (t / n * (1 + int(ph * 3)) + ph))
        if clear(x, y):
            put(f, x, y, mix((20, 30, 60), (170, 190, 230), k))
    for x0, y in STARS_NEAR:
        x = (x0 - 2 * t) % 64
        if clear(x, y):
            put(f, x, y, CREAM)
    d.ellipse((55, 0, 61, 6), fill=(230, 225, 190)); d.ellipse((57, -1, 63, 5), fill=BLACK)
    for k in range(2):
        r = ((t / n + k / 2) % 1.0) * 9 + 1
        arc(d, 7, 5, r, 200, 340, dim(AMB, max(0.2, 1 - r / 10)))
    d.line((7, 6, 7, 15), fill=(70, 75, 90)); d.line((4, 15, 7, 7), fill=(50, 55, 70)); d.line((10, 15, 7, 7), fill=(50, 55, 70))
    put(f, 7, 5, RED if t % 16 < 4 else (60, 10, 8))
    text35(f, 18, 3, "ORC RADIO", AMB)
    text35(f, 18, 10, "LATE NITE", LPB)
    return f


MORSE = {"O": "---", "R": ".-.", "C": "-.-."}


def _morse_tape():
    """(units[], letter_of_unit[]) for 'ORC' + word gap: 42 units."""
    units, owner = [], []
    for li, ch in enumerate("ORC"):
        for ei, sym in enumerate(MORSE[ch]):
            L = 1 if sym == "." else 3
            units += [1] * L; owner += [li] * L
            if ei < len(MORSE[ch]) - 1:
                units += [0]; owner += [li]
        g = 3 if li < 2 else 7
        units += [0] * g; owner += [-1] * g
    return units, owner


_MT, _MO = _morse_tape()
CURSOR = 44


@preset("p_orco_morse", "Morse ORC", "'ORC' keyed in Morse (--- .-. -.-.): a tape scrolls under a cursor, the lamp keys and the letter being sent lights up",
        n=len(_MT), ms=90, colors=8)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    u_at = lambda x: (t + (x - CURSOR) // 2) % n           # tape unit under screen column x
    cur = (t) % n
    li = _MO[cur]
    for i, (ch, c) in enumerate(zip("ORC", (OG, PB, AMB))):
        uni(f, i * 8, 0, ch, CREAM if i == li else dim(c, 0.55))
    text35(f, 27, 1, "MORSE", dim(LPB, 0.8))
    key = _MT[cur]
    d.ellipse((54, 0, 61, 7), outline=DAMB, fill=AMB if key else (40, 22, 0))
    if key:
        d.ellipse((56, 2, 59, 5), fill=CREAM)
    for x in range(26, 64):
        if _MT[u_at(x)]:
            sent = x < CURSOR
            d.line((x, 11, x, 12), fill=AMB if sent else dim(LPB, 0.6))
    d.line((CURSOR, 9, CURSOR, 14), fill=dim(OG2, 0.6))
    return f


EQ_PH = [random.Random(77 + i).random() for i in range(16)]


@preset("p_orco_eq", "Equalizer wordmark", "'OREGON RADIO CO' wordmark over 16 bouncing equalizer bars (green to amber, cream peaks); letters brighten with the bar below",
        n=32, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    hs = []
    for i in range(16):
        p = EQ_PH[i]
        v = 0.45 + 0.28 * math.sin(TAU * (t / n + p)) + 0.2 * math.sin(TAU * (2 * t / n + p * 2.7 + i * 0.13)) \
            + 0.12 * math.sin(TAU * (3 * t / n + i * 0.31))
        hs.append(max(1, min(9, round(v * 9))))
    for i, hgt in enumerate(hs):
        x = i * 4
        for k in range(hgt):
            y = 15 - k
            d.line((x, y, x + 2, y), fill=_lerp_stops([OG, OG, AMB], k / 8))
        d.line((x, 15 - hgt, x + 2, 15 - hgt), fill=CREAM)
    text35(f, 3, 0, "OREGON RADIO CO", lambda x, y: mix(dim(_lerp_stops([OG2, LPB, PB], x / 63), 0.55),
                                                       _lerp_stops([OG2, LPB, PB], x / 63), hs[min(15, x // 4)] / 9))
    return f


@preset("p_orco_coast", "Pacific coast signal", "Pacific waves rolling onto a headland lighthouse that blinks and sends radio arcs + 'OREGON COAST'",
        n=32, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    ph = t / n
    for x in range(64):
        crest = 11.6 + 1.1 * math.sin(TAU * (x / 32 + ph)) + 0.5 * math.sin(TAU * (x / 16 - 2 * ph))
        cy = int(round(crest))
        for y in range(max(0, cy), 16):
            f.putpixel((x, y), LPB if y == cy else mix(PB, DPB, (y - cy) / 4))
        if cy - 1 >= 0 and math.sin(TAU * (x / 32 + ph)) > 0.75:
            f.putpixel((x, cy), CREAM)
    d.polygon([(0, 8), (5, 8), (9, 11), (12, 15), (0, 15)], fill=DOG)
    d.line((0, 8, 5, 8), fill=OG)
    d.rectangle((3, 3, 4, 7), fill=CREAM); d.line((3, 5, 4, 5), fill=RED)
    lamp = (t % 8) < 2
    d.point([(3, 2), (4, 2)], fill=AMB if lamp else DAMB)
    for k in range(2):
        r = ((ph + k / 2) % 1.0) * 10 + 2
        arc(d, 4, 2, r, 300, 30, dim(AMB, max(0.2, 1 - r / 12)))
    text35(f, 16, 1, "OREGON COAST", grad(16, 47, [OG2, LPB]))
    return f


MASCOT = [
    "....HHHHHH....",
    "...HHHHHHHH...",
    "..HGGGGGGGGH..",
    "..GGGGGGGGGG..",
    "g.GBBBGGBBBG.g",
    "GgGEEgGGgEEGgG",
    ".GGGGGGGGGGGG.",
    "..GGGGggGGGG..",
    "..GGGgGGgGGG..",
    "..GWGGGGGGWG..",
    "..GWMMMMMMWG..",
    "..gGGGGGGGGg..",
    "...gGGGGGGg...",
    "....gggggg....",
    "..SSSSSSSSSS..",
    ".SSSSSSSSSSSS.",
]
MCOL = {"H": (45, 30, 20), "G": (70, 180, 60), "g": (30, 100, 30), "B": (25, 70, 20), "E": AMB,
        "W": CREAM, "M": (40, 10, 10), "S": DPB}


@preset("p_orco_mascot", "Orc mascot", "Pixel orc mascot holding a whip antenna that sends rings, blinking once per loop, beside 'ORC' and an ON AIR light",
        n=32, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    blink = t in (20, 21)
    for y, row in enumerate(MASCOT):
        for x, ch in enumerate(row):
            if ch != ".":
                c = MCOL["g"] if (blink and ch == "E") else MCOL[ch]
                f.putpixel((x, y), c)
    d.line((12, 14, 14, 12), fill=MCOL["G"])
    d.rectangle((14, 11, 15, 12), fill=MCOL["G"])
    d.line((15, 11, 18, 1), fill=(170, 170, 180))
    put(f, 18, 0, RED)
    for k in range(3):
        r = ((t / n + k / 3) % 1.0) * 7 + 1.5
        arc(d, 18, 0, r, 270, 90, dim(AMB, max(0.2, 1 - r / 9)))
    uni(f, 26, 0, "ORC", grad(26, 24, [OG2, OG, PB]))
    on = 2 <= (t % 16) < 14                     # toggles away from the loop seam
    d.rectangle((51, 1, 63, 15), outline=RED if on else (70, 12, 10))
    text35(f, 54, 3, "ON", RED if on else (70, 12, 10))
    text35(f, 52, 9, "AIR", RED if on else (70, 12, 10))
    return f


# ===================================================================== batch 2 (TheOrc.dev)
_BATCH = 2

# tiny mixed-case font for the domain 'TheOrc.dev' (6 rows, x-height 4)
DOM = {
    "T": ["###", ".#.", ".#.", ".#.", ".#.", ".#."], "h": ["#..", "#..", "##.", "#.#", "#.#", "#.#"],
    "e": ["...", "...", ".#.", "###", "#..", ".##"], "O": [".#.", "#.#", "#.#", "#.#", "#.#", ".#."],
    "r": ["...", "...", "#.#", "##.", "#..", "#.."], "c": ["...", "...", ".##", "#..", "#..", ".##"],
    ".": [".", ".", ".", ".", ".", "#"], "d": ["..#", "..#", ".##", "#.#", "#.#", ".##"],
    "v": ["...", "...", "#.#", "#.#", "#.#", ".#."],
}
DOMAIN = "TheOrc.dev"


def dom_width(s=DOMAIN):
    return sum(len(DOM[c][0]) for c in s) + len(s) - 1


def dom(f, x, y, s, color, upto=None):
    """Draw (the first `upto` chars of) s in the domain font; returns the x after the last char."""
    for i, ch in enumerate(s if upto is None else s[:upto]):
        g = DOM[ch]
        for r, row in enumerate(g):
            for c, v in enumerate(row):
                if v == "#":
                    put(f, x + c, y + r, color(x + c, y + r) if callable(color) else color)
        x += len(g[0]) + 1
    return x


DOM_GRAD = [OG2, LPB, AMB]


def border_path(h=16):
    """Outline pixels of the Oregon mask ordered along the real coastline/border ring."""
    ol = OR.outline(h)
    pix = [(x, y) for y in range(h) for x in range(ol.width) if ol.getpixel((x, y))]
    ring = max(OR.rings(), key=len)
    lon0, lat1, s_, k, w = OR._geom(h)
    pts = [((lon - lon0) * k, (lat1 - lat) * s_) for lon, lat in ring]
    cum = [0.0]
    for a, b in zip(pts, pts[1:]):
        cum.append(cum[-1] + math.dist(a, b))
    def param(p):
        c = (p[0] + 0.5, p[1] + 0.5)
        i = min(range(len(pts)), key=lambda j: (pts[j][0] - c[0]) ** 2 + (pts[j][1] - c[1]) ** 2)
        return cum[i]
    return sorted(pix, key=param)


_PATH = border_path(16)


def wire_vertices(h=14, step=1.6):
    ring = max(OR.rings(), key=len)
    lon0, lat1, s_, k, w = OR._geom(h)
    out = []
    for lon, lat in ring:
        p = ((lon - lon0) * k - w / 2, (lat1 - lat) * s_ - h / 2)
        if not out or math.dist(p, out[-1]) >= step:
            out.append(p)
    return out


_WIRE = wire_vertices()


@preset("p_orco_terminal", "TheOrc.dev terminal", "Shell prompt typing 'TheOrc.dev', the server answering 'HELLO FROM ORC', blinking cursor",
        n=37, ms=100, domain=True)
def _(t, n):
    f = canvas()
    tt = (t + 30) % n                      # seam sits inside the hold, the reset happens mid-loop
    typed = max(0, min(10, tt - 6 + 1)) if tt >= 6 else 0
    resp = max(0, min(14, (tt - 18 + 1) * 3)) if tt >= 18 else 0
    text35(f, 1, 1, ">", AMB)
    end = dom(f, 6, 0, DOMAIN, grad(6, dom_width(), DOM_GRAD), upto=typed)
    blink = tt % 4 < 2
    if resp == 0 and (typed < 10 or blink):
        ImageDraw.Draw(f).rectangle((end + (0 if typed == 0 else 0), 0, end + 2, 5), fill=OG2)
    if resp:
        text35(f, 1, 9, "HELLO FROM ORC"[:resp], OG)
        if resp >= 14 and blink:
            ImageDraw.Draw(f).rectangle((1, 15, 3, 15), fill=OG2)
    return f


@preset("p_orco_wireframe", "Oregon wireframe", "The real Oregon outline as an extruded 3D wireframe spinning about its vertical axis + 'OREGON' / 'TheOrc.dev'",
        n=24, ms=90, outline=True, domain=True)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    th = TAU * t / n
    cx, cy = 11.5, 8.0
    def proj(X, Y, Z):
        x = X * math.cos(th) + Z * math.sin(th)
        z = -X * math.sin(th) + Z * math.cos(th)
        p = 22 / (22 + z)
        return (cx + x * p, cy + Y * p), z
    back, front = [], []
    for X, Y in _WIRE:
        back.append(proj(X, Y, -2.0)); front.append(proj(X, Y, 2.0))
    zb = sum(z for _, z in back) / len(back); zf = sum(z for _, z in front) / len(front)
    near, far = (front, back) if zf < zb else (back, front)
    d.line([p for p, _ in far] + [far[0][0]], fill=DOG)
    for i in range(0, len(_WIRE), max(1, len(_WIRE) // 7)):
        d.line((far[i][0], near[i][0]), fill=dim(OG, 0.5))
    d.line([p for p, _ in near] + [near[0][0]], fill=OG2)
    text35(f, 26, 1, "OREGON", OG)
    dom(f, 25, 9, DOMAIN, grad(25, dom_width(), DOM_GRAD))
    return f


@preset("p_orco_tracer", "Border tracer", "A glowing comet tracing the real Oregon border (coast, Columbia, Snake, 42N) + 'OREGON / RADIO CO / LLC'",
        n=34, ms=80, outline=True)
def _(t, n):
    f = canvas()
    fm, ol, inner = state(16)
    paint(f, place(inner, 0, 0), BODY)
    paint(f, place(ol, 0, 0), dim(OG, 0.4))
    L = len(_PATH)
    head = t / n * L
    for k, c in enumerate([CREAM, AMB, AMB, DAMB, DAMB]):
        x, y = _PATH[int(head - k * 1.5) % L]
        put(f, x, y, c)
    text35(f, 26, 0, "OREGON", OG2)
    text35(f, 26, 6, "RADIO CO", LPB)
    text35(f, 26, 11, "LLC", AMB)
    return f


_RAIN = [(random.Random(500 + i).randrange(64), random.Random(900 + i).randrange(16)) for i in range(26)]


@preset("p_orco_rain", "Rain or shine", "Rain falling over Oregon while amber signal rings from Portland punch through it + 'RAIN OR / SHINE'",
        n=16, ms=90, outline=True)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    fm, ol, inner = state(16)
    paint(f, place(inner, 0, 0), BODY)
    paint(f, place(ol, 0, 0), dim(OG, 0.75))
    textbox = lambda x, y: 24 <= x <= 54 and (1 <= y <= 7 or 8 <= y <= 14)
    for x, y0 in _RAIN:
        y = (y0 + 2 * t) % 16
        for k, c in ((0, dim(LPB, 0.8)), (1, dim(PB, 0.45))):
            if not textbox(x, y - k):
                put(f, x, (y - k) % 16, c)
    px, py = city("Portland")
    for k in range(2):
        r = ((t / n + k / 2) % 1.0) * 14 + 1
        arc(d, px, py, r, 0, 360, AMB if r < 8 else DAMB)
    put(f, px, py, CREAM)
    text35(f, 25, 2, "RAIN OR", LPB)
    text35(f, 25, 9, "SHINE", AMB)
    return f


@preset("p_orco_crater", "Crater Lake ripple", "Crater Lake: caldera rim, deep blue water, Wizard Island, ripples spreading from a drop + 'CRATER / LAKE / 1949 FT'",
        n=24, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.ellipse((0, 0, 31, 15), fill=(70, 72, 66))
    d.ellipse((1, 1, 30, 14), fill=(44, 60, 48))
    d.ellipse((3, 2, 28, 13), fill=DPB)
    d.ellipse((6, 4, 25, 11), fill=(0, 60, 150))
    water = Image.new("L", (W, H)); ImageDraw.Draw(water).ellipse((3, 2, 28, 13), fill=255)
    lay = canvas(); ld = ImageDraw.Draw(lay)
    for k in range(3):
        u = (t / n + k / 3) % 1.0
        rx, ry = 1 + u * 13, 0.5 + u * 6
        ld.ellipse((18 - rx, 8 - ry, 18 + rx, 8 + ry), outline=mix(LPB, PB, u))
    m2 = Image.new("L", (W, H))
    m2.paste(water, (0, 0), lay.convert("L").point(lambda v: 255 if v else 0))
    paint(f, m2, lambda x, y: lay.getpixel((x, y)))
    d.polygon([(8, 11), (10, 8), (12, 11)], fill=DOG); put(f, 10, 8, OG)
    if t % 24 < 2:
        put(f, 18, 8, CREAM)
    text35(f, 35, 0, "CRATER", LPB)
    text35(f, 35, 6, "LAKE", OG2)
    text35(f, 35, 11, "1949 FT", dim(CREAM, 0.6))
    return f


@preset("p_orco_columbia", "Columbia data river", "The Columbia River flowing west under the gorge title, amber data packets riding the current between green banks",
        n=16, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    text35(f, 4, 0, "COLUMBIA GORGE", grad(4, 55, [LPB, OG2]))
    d.line((0, 6, 63, 6), fill=DOG)
    for y in range(7, 13):
        d.line((0, y, 63, y), fill=DPB if y in (7, 12) else (0, 55, 130))
    for x in range(64):
        for y, ph in ((8, 0), (10, 7), (11, 3)):
            if (x + t + ph) % 16 in (0, 1, 2):
                put(f, x, y, mix(PB, LPB, 0.4))
    for k in range(4):
        x = (k * 16 + 5 - 2 * t * 2 // 2) % 64
        y = (9, 10, 8, 11)[k]
        put(f, x, y, CREAM); put(f, x + 1, y, AMB); put(f, x + 2, y, AMB)
    d.line((0, 13, 63, 13), fill=OG)
    d.rectangle((0, 14, 63, 15), fill=DOG)
    for x in range(2, 64, 7):
        put(f, x, 13, OG2)
    return f


def _fir(d, x, base, h, col):
    for k in range(h):
        hw = (k * 2) // 5
        d.line((x - hw, base - h + k, x + hw, base - h + k), fill=col)


@preset("p_orco_firs", "Douglas fir antennas", "Two parallax layers of Douglas firs drifting by, a few treetops are antenna masts with blinking lights + 'TREETOP FM'",
        n=16, ms=100)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    text35(f, 13, 0, "TREETOP FM", grad(13, 39, [AMB, CREAM]))
    far_h = [6, 8, 7, 9]
    for i in range(10):
        x = (i * 8 - t) % 80 - 8
        _fir(d, x, 15, far_h[i % 4], (10, 60, 30))
    near_h = [9, 11, 8, 10]
    for i in range(6):
        x = (i * 16 + 4 - 2 * t) % 96 - 16
        h = near_h[i % 4]
        _fir(d, x, 16, h, (24, 130, 56))
        d.line((x, 16 - h, x, 15), fill=(30, 150, 60))
        if i % 2 == 0:
            d.line((x, 16 - h - 1, x, 16 - h - 3), fill=(170, 170, 180))
            put(f, x, 16 - h - 4, RED if (t + i) % 8 < 3 else (70, 12, 10))
    return f


def _print_mask():
    m = uni_mask("ORC")
    rows = [y for y in range(16) if any(m.getpixel((x, y)) for x in range(m.width))]
    return m, rows


_PM, _PROWS = _print_mask()
_PN = len(_PROWS) * 2 + 10


@preset("p_orco_printer", "3D print ORC", "A 3D-printer nozzle laying down 'ORC' layer by layer (hot amber layer cooling to green), percent readout, DONE",
        n=_PN, ms=100)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    L = len(_PROWS)
    tt = (t + 2 * L + 5) % n                  # seam inside the DONE hold
    x0, ybase = 3, 15 - (_PROWS[-1] + 1)      # letters sit on the bed (row 15)
    d.line((0, 15, 29, 15), fill=(110, 110, 120))
    layer, prog = (tt // 2, (tt % 2 + 1) / 2) if tt < 2 * L else (L, 1.0)
    done_rows = _PROWS[::-1][:layer]          # printed bottom-up
    for y in range(16):
        for x in range(_PM.width):
            if not _PM.getpixel((x, y)):
                continue
            if y in done_rows:
                put(f, x0 + x, ybase + y, OG)
    if layer < L:
        y = _PROWS[::-1][layer]
        xs = [x for x in range(_PM.width) if _PM.getpixel((x, y))]
        lo, hi = min(xs), max(xs)
        go_right = layer % 2 == 0
        reach = lo + (hi - lo) * prog if go_right else hi - (hi - lo) * prog
        for x in xs:
            if (x <= reach) if go_right else (x >= reach):
                put(f, x0 + x, ybase + y, AMB)
        nx, ny = x0 + round(reach), ybase + y - 1
        pct = int(100 * (layer + prog) / L)
        text35(f, 32, 1, "PRINTING", LPB)
        text35(f, 32, 8, f"{pct:3d}%", AMB)
    else:
        nx, ny = 28, 2
        text35(f, 32, 1, "DONE", OG2)
        text35(f, 32, 8, "100%", OG)
    d.rectangle((nx - 1, ny - 2, nx + 1, ny - 1), fill=(150, 150, 160))
    put(f, nx, ny, RED if layer < L else DAMB)
    d.line((0, 0, 29, 0), fill=(60, 60, 70))
    d.line((nx, 1, nx, ny - 3), fill=(60, 60, 70))
    return f


_BR = random.Random(4242)
_BITS = [_BR.random() < 0.5 for _ in range(32)]


@preset("p_orco_dongle", "SDR dongle", "RTL-style SDR dongle with blinking LEDs streaming bits out of its USB plug toward 'TheOrc.dev', '2.4 MSPS'",
        n=16, ms=90, domain=True)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.line((1, 0, 1, 5), fill=(170, 170, 180)); put(f, 1, 0, CREAM)
    d.rectangle((0, 6, 2, 9), fill=DAMB)
    d.rectangle((3, 4, 17, 11), fill=(32, 36, 44), outline=(90, 96, 112))
    text35(f, 5, 6, "SDR", dim(LPB, 0.8))
    put(f, 16, 5, OG2 if t % 4 < 2 else DOG)
    put(f, 16, 10, RED if t % 8 < 1 else (70, 12, 10))
    d.rectangle((18, 5, 21, 10), fill=(150, 150, 160)); d.line((19, 7, 20, 7), fill=(70, 70, 80))
    dom(f, 25, 0, DOMAIN, grad(25, dom_width(), DOM_GRAD))
    for x in range(23, 64):
        b = _BITS[((x - 23) // 2 - t) % 32]
        put(f, x, 8, AMB if b else (50, 30, 0))
    text35(f, 27, 11, "2.4 MSPS", dim(OG, 0.8))
    return f


_FIND = lambda d, x, y: (d.rectangle((x, y, x + 6, y + 6), outline=OG2), d.rectangle((x + 2, y + 2, x + 4, y + 4), fill=OG2))
_GL_LOCK = {}
_g = random.Random(31)
_GL_N = 34


@preset("p_orco_glitch", "QR glitch resolve", "QR-style finder squares and module noise that glitch-resolve into 'TheOrc.dev', hold, then dissolve again",
        n=_GL_N, ms=90, domain=True, key=0)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    tt = (t + 22) % n                            # seam inside the hold
    _FIND(d, 0, 0); _FIND(d, 0, 9); _FIND(d, 57, 0)
    txt = canvas(); tx = (64 - dom_width()) // 2
    dom(txt, tx, 5, DOMAIN, grad(tx, dom_width(), DOM_GRAD))
    # 0-5 noise, 6-15 resolve, 16-27 hold, 28-33 dissolve
    if tt < 6:
        clean = 0.0
    elif tt < 16:
        clean = (tt - 5) / 10
    elif tt < 28:
        clean = 1.0
    else:
        clean = 1 - (tt - 27) / 6
    rnd = random.Random(1000 + tt)
    for y in range(2, 14):
        for x in range(9, 56):
            lock = random.Random(x * 31 + y).random()
            if txt.getpixel((x, y)) != BLACK and lock < clean:
                put(f, x, y, txt.getpixel((x, y)))
            elif lock >= clean and rnd.random() < 0.28:
                put(f, x, y, dim(OG, 0.45) if rnd.random() < 0.7 else LPB)
    if clean >= 1:
        d.rectangle((57, 9, 63, 15), outline=dim(AMB, 0.6))
    return f


@preset("p_orco_scope", "Oscilloscope Lissajous", "Phosphor 3:2 Lissajous turning on a scope graticule, bright head tracing the curve + 'ORC LAB / X:Y 3:2'",
        n=32, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    for x in range(0, 32, 4):
        for y in range(0, 16, 4):
            put(f, x, y, (0, 40, 22))
    d.line((0, 8, 31, 8), fill=(0, 34, 18)); d.line((16, 0, 16, 15), fill=(0, 34, 18))
    ph = TAU * t / n
    X = lambda s_: 15.5 + 14 * math.sin(3 * s_ + ph)
    Y = lambda s_: 7.5 + 7 * math.sin(2 * s_)
    d.line([(X(s_), Y(s_)) for s_ in [TAU * i / 160 for i in range(161)]], fill=DOG)
    head = TAU * 2 * t / n
    for k in range(10):
        s_ = head - k * 0.06
        put(f, X(s_), Y(s_), OG2 if k < 3 else OG)
    text35(f, 36, 2, "ORC LAB", AMB)
    text35(f, 36, 9, "X:Y 3:2", LPB)
    return f


@preset("p_orco_satellite", "Satellite pass", "A satellite arcs across the sky over Oregon, downlinking to a Bend ground station; status LOS/TRK/ACQ + 'ORC SAT'",
        n=40, ms=90, outline=True)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    fm, ol, inner = state(16)
    paint(f, place(inner, 0, 0), BODY)
    paint(f, place(ol, 0, 0), dim(OG, 0.8))
    gx, gy = city("Bend")
    x = -10 + 84 * t / n
    y = 1 + 5 * ((x - 32) / 42) ** 2
    sx, sy = round(x), round(y)
    over = -1 <= sx <= 22
    put(f, gx, gy, AMB if (t % 4 < 2 or over) else DAMB)
    if over:
        for k in range(4):
            u = ((t / 4 + k / 4) % 1.0)
            put(f, sx + (gx - sx) * u, sy + (gy - sy) * u, mix(CREAM, AMB, u))
    for dx, c in ((-2, LPB), (-1, PB), (0, CREAM), (1, PB), (2, LPB)):
        put(f, sx + dx, sy, c)
    status = "ACQ" if over else ("TRK" if 0 <= sx < 64 else "LOS")
    text35(f, 24, 10, "ORC SAT", AMB)
    text35(f, 53, 10, status, OG2 if status == "ACQ" else (LPB if status == "TRK" else dim(RED, 0.8)))
    return f


SKY = [(10, 4, 30), (20, 8, 45), (35, 12, 60), (55, 16, 70), (80, 20, 70), (110, 30, 60), (150, 45, 45),
       (200, 70, 30), (240, 110, 30), (255, 150, 50)]


@preset("p_orco_sunset", "Pacific sunset AM", "Sun resting on the Pacific with a shimmering glint path, an amplitude-modulated AM wave rolling across the sky + 'ORC AM'",
        n=32, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    for y, c in enumerate(SKY):
        d.line((0, y, 63, y), fill=c)
    d.ellipse((40, 5, 52, 17), fill=(255, 220, 120))
    for y in range(10, 16):
        d.line((0, y, 63, y), fill=mix((10, 30, 80), DPB, (y - 10) / 5))
        w = 2 + round(2.5 * (0.5 + 0.5 * math.sin(TAU * (t / n * 2 + y * 0.37))))
        d.line((46 - w, y, 46 + w - (y % 2), y), fill=(255, 190, 90) if y < 13 else (200, 130, 60))
    ph = TAU * t / n
    for x in range(64):
        env = 0.55 + 0.45 * math.sin(TAU * x / 32 - ph)
        yv = 5 + 3 * env * math.sin(x * 1.1 - 2 * ph * 2)
        put(f, x, yv, CREAM)
    text35(f, 2, 11, "ORC AM", LPB)
    return f


def _glow(m, c, k=0.28):
    halo = m.filter(ImageFilter.MaxFilter(3))
    only = Image.new("L", m.size)
    only.paste(halo, (0, 0), m.point(lambda v: 255 - v))
    return only, dim(c, k)


@preset("p_orco_neon", "OPEN / ON AIR neon", "Neon shop sign: blue 'OPEN' tube with a glow that stutters, red 'ON AIR' buzzing, amber neon border",
        n=32, ms=100)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.rounded_rectangle((0, 0, 63, 15), radius=3, outline=AMB if t not in (25,) else DAMB)
    om = Image.new("L", (W, H)); om.paste(uni_mask("OPEN"), (3, 0))
    open_on = t not in (9, 10, 12)
    halo, hc = _glow(om, PB)
    if open_on:
        paint(f, halo, hc)
    paint(f, om, LPB if open_on else dim(PB, 0.35))
    am = Image.new("L", (W, H))
    tmp = canvas(); text35(tmp, 44, 3, "ON", (255, 255, 255)); text35(tmp, 42, 9, "AIR", (255, 255, 255))
    am = tmp.convert("L").point(lambda v: 255 if v else 0)
    air_k = 1.0 if t not in (20, 21) else 0.4
    ah, ac = _glow(am, RED, 0.25 * air_k)
    paint(f, ah, ac)
    paint(f, am, dim(RED, air_k))
    return f


def _coin_face(front: bool) -> Image.Image:
    im = Image.new("RGB", (16, 16))
    d = ImageDraw.Draw(im)
    d.ellipse((0, 0, 15, 15), outline=AMB, fill=(40, 22, 0) if front else BODY)
    if front:
        text35(im, 3, 5, "ORC", CREAM)
    else:
        fm, ol, _ = state(9)
        im.paste(Image.new("RGB", fm.size, OG), (2, 3), fm)
        im.paste(Image.new("RGB", ol.size, OG2), (2, 3), ol)
    return im


_COIN = (_coin_face(True), _coin_face(False))


@preset("p_orco_logospin", "Logo coin spin", "An 'ORC' coin spinning in 3D, the Oregon outline on its reverse, beside 'TheOrc.dev' and 'OREGON RADIO'",
        n=24, ms=90, outline=True, domain=True)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    c = math.cos(TAU * t / n)
    face = _COIN[0] if c >= 0 else _COIN[1].transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    w = max(1, round(16 * abs(c)))
    if w <= 2:
        d.line((8, 0, 8, 15), fill=AMB)
    else:
        img = face.resize((w, 16), Image.Resampling.NEAREST)
        f.paste(img, (8 - w // 2, 0))
    dom(f, 22, 1, DOMAIN, grad(22, dom_width(), DOM_GRAD))
    text35(f, 17, 10, "OREGON RADIO", OG)
    return f


# ===================================================================== build
def frames_of(entry):
    """Render all frames, then share one small palette (BLE budget: fewer colours -> shorter LZW codes)."""
    frames = [entry["fn"](t, entry["n"]).convert("RGB") for t in range(entry["n"])]
    sheet = Image.new("RGB", (W, H * len(frames)))
    for i, f in enumerate(frames):
        sheet.paste(f, (0, i * H))
    pal = sheet.quantize(colors=entry["colors"], method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    return [f.quantize(palette=pal, dither=Image.Dither.NONE).convert("RGB") for f in frames]


def build(entry) -> dict:
    n, name = entry["n"], entry["name"]
    frames = frames_of(entry)
    gif = encode_gif(frames, entry["ms"])
    (OUT / f"{name}.gif").write_bytes(gif)
    rep = frames[entry["key"]] if entry.get("key") is not None else max(frames, key=lambda f: sum(f.convert("L").tobytes()))
    rep.save(OUT / f"{name}.png")
    rep.resize((640, 160), Image.Resampling.NEAREST).save(OUT / f"{name}_preview_10x.png")
    meta = {"name": name, "kind": "gif", "file": f"{name}.gif", "still": f"{name}.png",
            "preview": f"{name}_preview_10x.png", "family": FAMILY, "title": entry["title"],
            "description": entry["desc"],
            "frames": n, "frame_ms": entry["ms"], "bytes": len(gif), "loop": "seamless (tools/loopcheck.py)",
            "live_data": False, "uses_oregon_outline": entry["outline"], "uses_theorc_dev": entry["domain"],
            "generator": f"tools/make_orco_presets.py::{name}"}
    (OUT / f"{name}.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    m = loop_metric_frames(frames)
    return {**meta, "_frames": frames, "_rep": rep, "_loop": m}


def contact_sheet(results, path):
    from orcpixels.studio import led_frame
    lab = ImageFont.truetype(str(ROOT / "orcpixels/fonts/unifont.otf"), 16)
    pad, lh, key_s, strip_s = 14, 40, 8, 3
    kw, kh = W * key_s, H * key_s
    sw, sh = W * strip_s, H * strip_s
    cols = 4
    width = pad + kw + pad + cols * (sw + 6) + pad
    row_h = lh + kh + pad
    sheet = Image.new("RGB", (width, 40 + len(results) * row_h + pad), (22, 22, 24))
    d = ImageDraw.Draw(sheet)
    d.text((pad, 10), f"OrcPixels  orc_co  -  Oregon Radio Company / TheOrc.dev ({len(results)} seamless loops, LED-dot render)",
           fill=(255, 236, 190), font=lab)
    y = 40
    for r in results:
        fr = r["_frames"]
        tag = ("  [Oregon outline]" if r["uses_oregon_outline"] else "") + ("  [TheOrc.dev]" if r["uses_theorc_dev"] else "")
        d.text((pad, y), f"{r['name']}  -  {r['title']}{tag}", fill=(150, 245, 160), font=lab)
        d.text((pad, y + 18), f"{r['frames']} frames x {r['frame_ms']} ms, {r['bytes'] / 1024:.1f} KB, "
               f"loop seam {r['_loop']['seam']} / p90 {r['_loop']['p90']}: "
               f"{'seamless' if r['_loop']['ok'] else 'JUMP'}", fill=(170, 190, 210), font=lab)
        sheet.paste(led_frame(r["_rep"], key_s), (pad, y + lh))
        picks = [round(i * len(fr) / (2 * cols)) for i in range(2 * cols)]
        for j, fi in enumerate(picks):
            cx = pad + kw + pad + (j % cols) * (sw + 6)
            cy = y + lh + (j // cols) * (sh + 8)
            sheet.paste(led_frame(fr[fi], strip_s), (cx, cy))
        y += row_h
    sheet.save(path)
    return path


def preview_gif(results, path, frames=90, ms=100):
    from orcpixels.studio import led_frame
    lab = ImageFont.truetype(str(ROOT / "orcpixels/fonts/unifont.otf"), 16)
    s, cols, pad, lh = 4, 3, 10, 18
    tw, th = W * s, H * s
    rows = math.ceil(len(results) / cols)
    gw, gh = pad + cols * (tw + pad), pad + rows * (th + lh + pad)
    base = Image.new("RGB", (gw, gh), (22, 22, 24))
    d = ImageDraw.Draw(base)
    for i, r in enumerate(results):
        d.text((pad + (i % cols) * (tw + pad), pad + (i // cols) * (th + lh + pad)), r["name"][7:],
               fill=(150, 245, 160), font=lab)
    out = []
    for k in range(frames):
        im = base.copy()
        for i, r in enumerate(results):
            fr = r["_frames"]
            fi = int(k * ms / r["frame_ms"]) % len(fr)
            im.paste(led_frame(fr[fi], s), (pad + (i % cols) * (tw + pad), pad + lh + (i // cols) * (th + lh + pad)))
        out.append(im)
    # one shared palette from a sample of frames (per-frame adaptive palettes rendered grey in some viewers)
    sample = Image.new("RGB", (gw, gh * 6))
    for i in range(6):
        sample.paste(out[i * len(out) // 6], (0, i * gh))
    pal = sample.quantize(colors=255, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    q = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in out]
    q[0].save(path, save_all=True, append_images=q[1:], duration=ms, loop=0, optimize=False)
    return path


def main(argv):
    sheet = preview = None
    if "--sheet" in argv:
        sheet = argv[argv.index("--sheet") + 1]
    if "--preview" in argv:
        preview = argv[argv.index("--preview") + 1]
    names = [a for a in argv if a.startswith("p_orco_")]
    batch = int(argv[argv.index("--batch") + 1]) if "--batch" in argv else None
    sel = [e for e in REGISTRY if (not names or e["name"] in names) and (batch is None or e["batch"] == batch)]
    results = []
    for e in sel:
        r = build(e)
        results.append(r)
        m = r["_loop"]
        print(f"{r['name']:20} {r['frames']:3}f x{r['frame_ms']}ms {r['bytes']:6} B  seam {m['seam']:5} p90 {m['p90']:5} "
              f"mc {m['seam_mc']:5}/{m['p90_mc']:5} {'seamless' if m['ok'] else 'JUMP'}  {'[OR]' if r['uses_oregon_outline'] else ''}{'[DEV]' if r['uses_theorc_dev'] else ''}")
    if sheet:
        print("sheet", contact_sheet(results, sheet))
    if preview:
        print("preview", preview_gif(results, preview))
    print(len(results), "presets")


if __name__ == "__main__":
    main(sys.argv[1:])
