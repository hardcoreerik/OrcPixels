"""'RF / Signal' presets (family rf_sig, p_sig_<name>): the Midnight Synth concept
boards (2026-10-14), hand-built as real 64x16 and 64x64 pixel art.

Each concept is native at the size it was drawn for on the boards and has a hand-laid
companion at the other size, so it shows up in both preset lists (presets/ = 64x16,
presets64/ = 64x64, like every other family):

    64x16 concepts: fox hunt, jamming storm, modulation waveform, transmitter beacon, analog tuner
    64x64 concepts: satellite uplink, digital sync grid, frequency hopping, spectrum analyzer, APRS decode

Palette: Midnight Synth (black sky, blue / violet / magenta / ice), solid shapes only: no
dim noise fields, so it reads on real LEDs. Every motion is a function of the phase t/n
with integer cycles (scrolls wrap by exactly their period, fades end where they start),
checked by orcpixels.loopmetric. Art only: nothing here claims live data, and the APRS
screen uses the documentation placeholder callsign N0CALL.

    python tools/make_rfsig_presets.py                    # build all (both sizes)
    python tools/make_rfsig_presets.py p_sig_foxhunt      # build selected
    python tools/make_rfsig_presets.py --sheet OUT.png --preview OUT.gif
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from orcpixels.loopmetric import loop_metric_frames  # noqa: E402
from orcpixels.render import _lerp_stops, encode_gif  # noqa: E402
from pixfont import text35, width35  # noqa: E402

OUT16, OUT64 = ROOT / "presets", ROOT / "presets64"
FAMILY = "rf_sig"
TAU = 2 * math.pi
BLACK = (0, 0, 0)
# Midnight Synth palette
WHITE, ICE, CYAN = (255, 255, 255), (190, 230, 255), (60, 200, 255)
BLUE, DBLUE, NAVY = (40, 100, 255), (24, 50, 170), (22, 22, 92)
VIOLET, LVIOLET, DVIOLET = (140, 60, 255), (190, 140, 255), (60, 30, 140)
MAG, PINK, DMAG = (255, 50, 210), (255, 150, 235), (130, 20, 120)
MOUNT, MOUNT2, RIDGE = (30, 28, 110), (44, 34, 140), (110, 70, 255)
WARM, LAMP = (255, 190, 90), (255, 40, 120)
SYNTH = [BLUE, CYAN, VIOLET, MAG]          # left -> right accent ramp
HEAT = [DBLUE, BLUE, CYAN, VIOLET, MAG, PINK, WHITE]

REGISTRY: list[dict] = []


def preset(name, size, title, desc, n, ms=100, colors=32, native=True, note=""):
    """Register one size of preset `name`. Title/description come from the native entry."""
    def deco(fn):
        REGISTRY.append(dict(name=name, size=size, title=title, desc=desc, n=n, ms=ms, fn=fn,
                             colors=colors, native=native, note=note))
        return fn
    return deco


# ===================================================================== helpers
def canvas(w=64, h=16, bg=BLACK):
    return Image.new("RGB", (w, h), bg)


def dim(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def mix(a, b, k):
    k = max(0.0, min(1.0, k))
    return tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3))


def ramp(stops, k):
    return _lerp_stops(stops, max(0.0, min(1.0, k)))


def put(img, x, y, c):
    x, y = int(round(x)), int(round(y))
    if 0 <= x < img.width and 0 <= y < img.height:
        img.putpixel((x, y), c)


def add(img, x, y, c):
    """Additive put (glows over what is there)."""
    x, y = int(round(x)), int(round(y))
    if 0 <= x < img.width and 0 <= y < img.height:
        o = img.getpixel((x, y))
        img.putpixel((x, y), tuple(min(255, o[i] + c[i]) for i in range(3)))


def pulse(t, n, cycles=1, ph=0.0):
    """0..1..0 smooth, `cycles` integer times per loop."""
    return 0.5 - 0.5 * math.cos(TAU * (cycles * t / n + ph))


def stars(seed, count, w, h, ymax=None):
    r = random.Random(seed)
    return [(r.randrange(w), r.randrange(ymax or h), r.random(), r.random()) for _ in range(count)]


def twinkle(img, pts, t, n, cols=(ICE, LVIOLET, CYAN), lo=0.35):
    """Stars twinkle (integer cycles); none ever drops to a dim speck below `lo`."""
    for i, (x, y, ph, c) in enumerate(pts):
        k = lo + (1 - lo) * pulse(t, n, 1 + i % 2, ph)
        put(img, x, y, dim(cols[int(c * len(cols)) % len(cols)], k))


def sparkle(img, x, y, c, arm=1, core=WHITE):
    put(img, x, y, core)
    for a in range(1, arm + 1):
        k = 1 - (a - 1) / (arm + 0.5)
        for dx, dy in ((a, 0), (-a, 0), (0, a), (0, -a)):
            put(img, x + dx, y + dy, dim(c, k))


def dotted_line(img, x0, y0, x1, y1, c, step=2, phase=0.0, bright=None, every=0):
    """Dots every `step` px along a segment; `phase` (0..1) slides them along (loops when it
    goes 0 -> 1). With `every`, each every-th dot is a brighter 'packet'."""
    L = math.hypot(x1 - x0, y1 - y0)
    k = int(L // step)
    for i in range(k + 1):
        s = (i + phase) * step
        if s > L:
            continue
        x, y = x0 + (x1 - x0) * s / L, y0 + (y1 - y0) * s / L
        col = bright if (bright and every and i % every == 0) else c
        if col is not None:
            put(img, x, y, col)


def dotted_ring(img, cx, cy, r, c, gap=2.2, sy=1.0, a0=0, a1=TAU, pts=None):
    if r < 0.6:
        put(img, cx, cy, c)
        return
    m = max(4, int(TAU * r / gap))
    for i in range(m):
        a = i * TAU / m
        if a0 <= a <= a1 or (a0 < 0 and a >= a0 + TAU):
            put(img, cx + r * math.cos(a), cy + r * math.sin(a) * sy, c)


def ridge_line(w, seed, base, amp, peaks=4):
    """Mountain profile y(x): a few smooth bumps (deterministic)."""
    r = random.Random(seed)
    bumps = [(r.uniform(0, w), r.uniform(0.4, 1.0) * amp, r.uniform(w / 10, w / 5)) for _ in range(peaks)]
    out = []
    for x in range(w):
        v = 0.0
        for bx, ba, bw in bumps:
            v = max(v, ba * max(0.0, 1 - abs(x - bx) / bw))
        out.append(int(round(base - v)))
    return out


def mountains(img, prof, fill=MOUNT, rim=RIDGE, ybottom=None):
    d = ImageDraw.Draw(img)
    yb = img.height - 1 if ybottom is None else ybottom
    for x, y in enumerate(prof):
        if y <= yb:
            d.line((x, y, x, yb), fill=fill)
            put(img, x, y, rim)


def pines(img, y_base, xs_heights, fill=BLACK, rim=None):
    """Pine silhouettes (triangles) standing on y_base; rim = lit tip colour."""
    for x, h in xs_heights:
        for k in range(h):
            half = (k * 2) // 3 // 2 + (k // 4)
            y = y_base - h + 1 + k
            for dx in range(-half, half + 1):
                put(img, x + dx, y, fill)
        if rim:
            put(img, x, y_base - h + 1, rim)


def tower(img, x, ybase, h, col, lamp=None, lamp_k=1.0):
    d = ImageDraw.Draw(img)
    spread = max(1, h // 4)
    d.line((x - spread, ybase, x, ybase - h), fill=col)
    d.line((x + spread, ybase, x, ybase - h), fill=col)
    for k in range(2, h - 1, 3):
        w = max(0, round(spread * (1 - k / h)))
        d.line((x - w, ybase - k, x + w, ybase - k), fill=dim(col, 0.75))
    if lamp:
        put(img, x, ybase - h - 1, dim(lamp, lamp_k))


def dish(img, cx, cy, r, tilt=-0.65, body=(70, 64, 150), rim=LVIOLET, inner=(34, 30, 100), mount=(110, 100, 190),
         mount_h=None, glow=None, ribs=True):
    """Parabolic dish seen obliquely: an ellipse face (major axis r across the aperture, minor
    0.45 r along the boresight) opening toward `tilt` (rad, 0 = right, -pi/2 = up), feed struts to
    the focus and an A-frame pedestal. Returns the feed (focus) point."""
    ux, uy = math.cos(tilt), math.sin(tilt)        # boresight
    vx, vy = -uy, ux                                # across the aperture
    d = ImageDraw.Draw(img)
    mh = mount_h if mount_h is not None else r
    # pedestal first (behind the dish)
    by = cy + mh
    d.line((cx, cy, cx - max(1, r // 3), by), fill=mount)
    d.line((cx, cy, cx + max(1, r // 3), by), fill=mount)
    d.line((cx - max(1, r // 2), by, cx + max(1, r // 2), by), fill=mount)
    pts = []
    for i in range(48):
        a = TAU * i / 48
        pts.append((cx + vx * r * math.cos(a) + ux * 0.45 * r * math.sin(a),
                    cy + vy * r * math.cos(a) + uy * 0.45 * r * math.sin(a)))
    d.polygon(pts, fill=inner)
    if ribs and r >= 6:
        for k in range(0, 48, 8):
            d.line((cx, cy, *pts[k]), fill=body)
    d.line(pts + [pts[0]], fill=rim)
    fx, fy = cx + ux * r * 0.75, cy + uy * r * 0.75
    for k in (0, 24):
        d.line((*pts[k], fx, fy), fill=dim(mount, 0.9))
    put(img, fx, fy, glow or WHITE)
    return fx, fy


def satellite(img, x, y, s=1, body=(200, 190, 255), panel=BLUE, grid=DBLUE, blink=None):
    """Satellite centred at (x, y); s=1 -> ~13x5, s=2 -> ~26x10."""
    d = ImageDraw.Draw(img)
    pw, ph, bw = 5 * s, 3 * s if s > 1 else 3, 2 * s
    for side in (-1, 1):
        x0 = x + side * (bw + 1) if side > 0 else x - bw - 1 - pw + 1
        x0 = int(x0) if side > 0 else int(x - bw - pw)
        d.rectangle((x0, y - ph // 2, x0 + pw - 1, y - ph // 2 + ph - 1), fill=panel)
        if s > 1:
            for gx in range(x0 + 2, x0 + pw - 1, 3):
                d.line((gx, y - ph // 2, gx, y - ph // 2 + ph - 1), fill=grid)
            d.line((x0, y, x0 + pw - 1, y), fill=grid)
    d.line((x - bw - 1, y, x + bw + 1, y), fill=dim(body, 0.7))
    d.ellipse((x - bw + 1, y - bw, x + bw - 1, y + bw), fill=body)
    if blink is not None:
        put(img, x, y - bw - 1, blink)


N4 = ["#..#", "##.#", "#.##", "#..#", "#..#"]


def txt(img, x, y, s, c):
    """pixfont 3x5 text, but with a 4-wide N (the shared 3x5 N reads as W/H at LED size)."""
    for ch in s:
        if ch == "N":
            for ry, row in enumerate(N4):
                for rx, v in enumerate(row):
                    if v == "#":
                        put(img, x + rx, y + ry, c)
            x += 5
        else:
            x = text35(img, x, y, ch, c) + 1
    return x - 1


def txt_w(s):
    return sum(5 if ch == "N" else 4 for ch in s) - 1


# ----------------------------------------------------------------- shared sprites
FOX = [
    "............#.#.",
    "............###.",
    "...........#####",
    "...#########.##.",
    "..##########....",
    ".###.######.....",
    "##...#.#..#.#...",
    "#....#.#..#.#...",
]
FOX_TIP = {(0, 6), (0, 7), (1, 6)}


def fox(img, x, y, s=1, fill=(255, 70, 170), hi=(255, 170, 230), tail_up=0):
    rows = [list(r) for r in FOX]
    tip = set(FOX_TIP)
    if tail_up:                                     # tail flicks up one row
        rows[6][0] = rows[6][1] = rows[7][0] = "."
        rows[4][0] = rows[5][0] = "#"
        tip = {(0, 4), (0, 5)}
    h, w = len(rows), len(rows[0])
    for ry in range(h):
        for rx in range(w):
            if rows[ry][rx] != "#":
                continue
            top = ry == 0 or rows[ry - 1][rx] != "#"
            c = WHITE if (rx, ry) in tip else (hi if top else fill)
            for a in range(s):
                for b in range(s):
                    put(img, x + rx * s + a, y + ry * s + b, c if (b == 0 or not top or (rx, ry) in tip) else fill)
    put(img, x + 14 * s, y + 2 * s, BLACK)          # eye


def spiral_field(w, h, cx, cy, rx, ry, rot, twist=2.4, core=1.0):
    """Two-armed spiral galaxy brightness 0..1 for each pixel (rot = arm angle offset)."""
    out = {}
    for y in range(h):
        for x in range(w):
            dx, dy = (x - cx) / rx, (y - cy) / ry
            r = math.hypot(dx, dy)
            if r > 1.05:
                continue
            a = math.atan2(dy, dx)
            arm = 0.5 + 0.5 * math.cos(2 * (a - rot) - twist * math.log(r + 0.05) * 2)
            v = (arm ** 6) * (1.15 - r) * 1.5 + core * max(0.0, 1 - r * 3.5) ** 1.5
            if v > 0.16:
                out[(x, y)] = min(1.0, v)
    return out


def galaxy(img, cx, cy, rx, ry, rot):
    stops = [DVIOLET, VIOLET, MAG, PINK, WHITE]
    for (x, y), v in spiral_field(img.width, img.height, cx, cy, rx, ry, rot).items():
        q = round(v * 5) / 5
        if q >= 0.2:
            put(img, x, y, ramp(stops, q))


def bolt(seed, x0, y0, y1, wobble=2):
    r = random.Random(seed)
    pts, x = [], x0
    for y in range(y0, y1 + 1):
        pts.append((x, y))
        x += r.choice((-1, 0, 1)) * (1 if r.random() < 0.7 else wobble)
    return pts


def draw_bolt(img, pts, k, branch_seed=None):
    c, core = dim(LVIOLET, k), dim(WHITE, k)
    for i, (x, y) in enumerate(pts):
        put(img, x - 1, y, c)
        put(img, x, y, core)
    if branch_seed is not None:
        r = random.Random(branch_seed)
        i0 = len(pts) // 3
        bx, by = pts[i0]
        for j in range(len(pts) // 3):
            bx += r.choice((1, 1, 2)) * (1 if branch_seed % 2 else -1)
            by += 1
            put(img, bx, by, c)


def flash(t, n, at, length=2):
    """Lightning envelope: 1 on frames at..at+length-1 (mod n), else 0."""
    return 1.0 if (t - at) % n < length else (0.45 if (t - at) % n == length else 0.0)


def bars_values(count, t, n, seed, peaks, lo=0.15, amp=0.22):
    r = random.Random(seed)
    out = []
    for i in range(count):
        x = i / max(1, count - 1)
        base = lo + sum(pk * math.exp(-((x - px) / pw) ** 2) for px, pk, pw in peaks)
        f, ph = 1 + r.randrange(3), r.random()
        out.append(max(0.06, min(1.0, base + amp * (0.4 + base) * math.sin(TAU * (f * t / n + ph)))))
    return out


# ===================================================================== 64x16 concepts
# ---------------------------------------------------------------- 01 fox hunt
_FOX_STARS16 = stars(101, 14, 64, 16, ymax=8)
_FOX_RIDGE16 = ridge_line(64, 7, 13, 5, peaks=6)
FOX_T, FOX_D = "Fox hunt", ("Midnight fox on the ridge with a direction-finding target pulsing over the hills "
                            "and a blinking transmitter (the 'fox') on the far peak")


@preset("p_sig_foxhunt", (64, 16), FOX_T, FOX_D, n=24, colors=24,
        note="moon left, fox on the ridge, DF rings centre, tower right")
def _(t, n):
    f = canvas()
    twinkle(f, _FOX_STARS16, t, n)
    d = ImageDraw.Draw(f)
    d.ellipse((3, 1, 7, 5), fill=(230, 235, 255))
    put(f, 4, 2, ICE)
    mountains(f, _FOX_RIDGE16, fill=NAVY, rim=DVIOLET)
    d.line((0, 14, 63, 14), fill=(60, 44, 180))
    d.line((0, 15, 63, 15), fill=BLACK)
    pines(f, 15, [(1, 5), (5, 4), (9, 6), (40, 4), (47, 5), (51, 3), (62, 4)], rim=None)
    # DF target: rings expanding from the bearing point
    cx, cy = 37, 6
    for k in range(2):
        r = 2 + 3.2 * (k + t / n)
        if r < 8.2:
            c = mix(WHITE, VIOLET, (r - 2) / 6.2)
            dotted_ring(f, cx, cy, r, dim(c, 1.0 if r < 6.5 else 0.6), gap=1.6)
    sparkle(f, cx, cy, LVIOLET, arm=2)
    # bearing from the fox's nose to the target
    dotted_line(f, 28, 9, cx - 3, cy + 1, dim(MAG, 0.8), step=3, phase=t / n * 3 % 1)
    fox(f, 11, 7, tail_up=(t // 6) % 2)
    tower(f, 59, 14, 9, MAG, lamp=LAMP, lamp_k=0.4 + 0.6 * pulse(t, n, 2))
    return f


@preset("p_sig_foxhunt", (64, 64), FOX_T, FOX_D, n=24, colors=40, native=False,
        note="square: big DF target in the sky, fox (2x) on the ridge, tower on the right peak")
def _(t, n):
    f = canvas(64, 64)
    twinkle(f, _FOX64_STARS, t, n)
    d = ImageDraw.Draw(f)
    d.ellipse((4, 4, 13, 13), fill=(230, 235, 255))
    d.point([(7, 7), (9, 10), (10, 6)], fill=ICE)
    cx, cy = 40, 18
    for k in range(3):
        r = 3 + 4.5 * (k + t / n)
        if r < 15.5:
            c = mix(WHITE, VIOLET, (r - 3) / 12.5)
            dotted_ring(f, cx, cy, r, dim(c, 1.0 if r < 12.5 else 0.6), gap=1.7)
    d.line((cx - 15, cy, cx - 6, cy), fill=dim(VIOLET, 0.8))
    d.line((cx + 6, cy, cx + 15, cy), fill=dim(VIOLET, 0.8))
    sparkle(f, cx, cy, LVIOLET, arm=3)
    mountains(f, _FOX64_BACK, fill=NAVY, rim=DVIOLET)
    mountains(f, _FOX64_RIDGE, fill=MOUNT, rim=RIDGE)
    d.rectangle((0, 55, 63, 63), fill=BLACK)
    d.line((0, 54, 63, 54), fill=(70, 50, 200))
    pines(f, 63, [(36, 8), (42, 6), (48, 9), (55, 10), (61, 8)], fill=(24, 20, 88), rim=DVIOLET)
    dotted_line(f, 34, 42, cx - 4, cy + 4, dim(MAG, 0.85), step=3, phase=t / n * 3 % 1)
    fox(f, 2, 38, s=2, tail_up=(t // 6) % 2)
    tower(f, 56, 40, 14, MAG, lamp=LAMP, lamp_k=0.4 + 0.6 * pulse(t, n, 2))
    return f


_FOX64_STARS = stars(102, 34, 64, 64, ymax=36)
_FOX64_BACK = ridge_line(64, 11, 42, 10, peaks=5)
_FOX64_RIDGE = ridge_line(64, 12, 54, 9, peaks=4)


# ---------------------------------------------------------------- 02 jamming storm
JAM_T, JAM_D = "Jamming storm", ("A violet spiral storm over the ground station: lightning cracks, interference "
                                 "streaks race across the band and the dish rides it out")
_JAM_STREAKS16 = [(y, x, L, c) for (y, x, L, c) in
                  [(3, 4, 9, MAG), (5, 40, 12, CYAN), (8, 20, 7, PINK), (10, 50, 10, VIOLET), (2, 30, 5, ICE),
                   (11, 8, 6, CYAN), (6, 58, 8, MAG)]]


def streaks(f, lst, t, n, speed):
    for y, x, L, c in lst:
        x0 = (x - speed * t) % f.width
        for i in range(L):
            k = 1.0 if i == 0 else max(0.35, 1 - i / L)
            put(f, (x0 + i) % f.width, y, dim(c, k))


@preset("p_sig_jamstorm", (64, 16), JAM_T, JAM_D, n=32, colors=16,
        note="dish + lit station left, rotating spiral centre, lightning, streaks scroll left")
def _(t, n):
    f = canvas()
    streaks(f, _JAM_STREAKS16, t, n, 2)
    galaxy(f, 38, 6.5, 15, 6.5, TAU * t / n / 2)
    for at, x0, seed in ((3, 22, 5), (13, 55, 8), (23, 30, 3)):
        k = flash(t, n, at)
        if k:
            draw_bolt(f, bolt(seed, x0, 0, 12), k)
    d = ImageDraw.Draw(f)
    d.line((0, 15, 63, 15), fill=(16, 14, 60))
    pines(f, 15, [(16, 4), (20, 5), (26, 3), (48, 4), (53, 6), (58, 4), (63, 5)])
    d.rectangle((0, 12, 12, 15), fill=(24, 20, 70))
    for wx in (2, 5, 9):
        put(f, wx, 13, WARM if (wx + t // 8) % 3 else MAG)
    dish(f, 8, 6, 5, tilt=-0.75, mount_h=5, glow=dim(PINK, 0.5 + 0.5 * pulse(t, n, 2)))
    return f


@preset("p_sig_jamstorm", (64, 64), JAM_T, JAM_D, n=32, colors=48, native=False,
        note="square: big spiral storm, three lightning bolts, streak band, dish station bottom-left")
def _(t, n):
    f = canvas(64, 64)
    twinkle(f, _JAM64_STARS, t, n, lo=0.4)
    galaxy(f, 36, 22, 26, 17, TAU * t / n / 2)
    streaks(f, _JAM64_STREAKS, t, n, 2)
    for at, x0, seed in ((2, 12, 21), (12, 54, 9), (22, 28, 14)):
        k = flash(t, n, at)
        if k:
            draw_bolt(f, bolt(seed, x0, 0, 40), k, branch_seed=seed)
    mountains(f, _JAM64_RIDGE, fill=MOUNT, rim=RIDGE)
    d = ImageDraw.Draw(f)
    d.rectangle((0, 56, 63, 63), fill=BLACK)
    pines(f, 63, [(28, 10), (34, 8), (40, 12), (47, 9), (54, 11), (60, 8)])
    d.rectangle((1, 52, 22, 63), fill=(24, 20, 70))
    d.line((1, 52, 22, 52), fill=DVIOLET)
    for i, wx in enumerate((4, 9, 14, 19)):
        c = WARM if (i + t // 8) % 4 else MAG
        d.rectangle((wx, 55, wx + 1, 56), fill=c)
        d.rectangle((wx, 59, wx + 1, 60), fill=dim(c, 0.75) if i % 2 else BLACK)
    dish(f, 13, 36, 10, tilt=-0.8, mount_h=9, glow=dim(PINK, 0.5 + 0.5 * pulse(t, n, 2)))
    return f


_JAM64_STARS = stars(201, 20, 64, 64, ymax=44)
_JAM64_RIDGE = ridge_line(64, 23, 52, 8, peaks=5)
_JAM64_STREAKS = [(42, 4, 12, MAG), (44, 30, 16, CYAN), (46, 50, 10, PINK), (40, 20, 8, VIOLET), (47, 12, 9, ICE),
                  (43, 56, 12, VIOLET), (38, 44, 7, CYAN)]


# ---------------------------------------------------------------- 03 modulation waveform
MOD_T, MOD_D = "Modulation waveform", ("Two dotted carriers twist past each other under a travelling AM envelope, "
                                       "blue to magenta, with emitter rings at both ends")


def trace(f, pts, c_fn, width_fn=None):
    """Continuous polyline through [(x, y)] with per-x colour; vertical gaps are bridged."""
    d = ImageDraw.Draw(f)
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        d.line((x0, round(y0), x1, round(y1)), fill=c_fn(x0))


def waves_pair(f, y0, amp_fn, k, t, n, step_shift, x0=0, x1=63, stops=SYNTH, period=64):
    """Two mirrored carriers (+/- sin) under an envelope; the pattern scrolls `step_shift` px a frame
    and repeats every `period` px, so step_shift * n == period loops exactly."""
    off = step_shift * t
    for x in range(x0, x1 + 1, 3):
        put(f, x, y0, (40, 40, 150))
    a_pts, b_pts = [], []
    for x in range(x0, x1 + 1):
        xs = x + off
        a = amp_fn(xs)
        sv = math.sin(TAU * k * xs / period)
        a_pts.append((x, y0 + a * sv))
        b_pts.append((x, y0 - a * sv))
    trace(f, b_pts, lambda x: mix(ramp(stops, (x - x0) / max(1, x1 - x0)), MAG, 0.45))
    trace(f, a_pts, lambda x: ramp(stops, (x - x0) / max(1, x1 - x0)))
    for (x, ya), (_, yb) in zip(a_pts, b_pts):          # crossings glow white
        if abs(ya - yb) < 0.9:
            put(f, x, ya, WHITE)


def end_rings(f, cx, cy, t, n, rmax=6, right=False, sy=1.0):
    """Half rings opening outward at a trace end: two rings, each expanding once per half loop."""
    for kk in range(2):
        r = 1 + (rmax - 1) * ((kk + t / n * 2) % 2) / 2
        m = max(4, int(TAU * r / 2.0))
        for i in range(m):
            a = i * TAU / m - math.pi
            if (not right and math.cos(a) < 0.2) or (right and math.cos(a) > -0.2):
                put(f, cx + r * math.cos(a), cy + r * math.sin(a) * sy,
                    dim(mix(CYAN, VIOLET, r / rmax), 1 - 0.6 * r / rmax))
    put(f, cx, cy, WHITE)


@preset("p_sig_modwave", (64, 16), MOD_T, MOD_D, n=32, colors=16,
        note="carrier pair across the full width, envelope travels, rings at both ends")
def _(t, n):
    f = canvas()
    waves_pair(f, 7.5, lambda xs: 1.0 + 5.5 * pulse(xs, 64, 1), 4, t, n, 2, x0=7, x1=56)
    end_rings(f, 2, 7.5, t, n, rmax=7)
    end_rings(f, 61, 7.5, t, n, rmax=7, right=True)
    return f


@preset("p_sig_modwave", (64, 64), MOD_T, MOD_D, n=32, colors=40, native=False,
        note="square scope: AM, FM and PM traces stacked, all scrolling one screen per loop")
def _(t, n):
    f = canvas(64, 64)
    for y in (21, 42):
        for x in range(0, 64, 2):
            put(f, x, y, (30, 24, 100))
    off = 2 * t
    rows = [("AM", 10.5), ("FM", 31.5), ("PM", 52.5)]
    for name, y0 in rows:
        text35(f, 1, int(y0) - 9, name, LVIOLET)
        for x in range(14, 64, 3):
            put(f, x, y0, (40, 40, 150))
        pts, mir = [], []
        for x in range(14, 64):
            xs = x + off
            if name == "AM":
                a = 1.0 + 6.5 * pulse(xs, 64, 1)
                ph = TAU * 4 * xs / 64
            elif name == "FM":
                a = 6.5
                ph = TAU * (5 * xs / 64) + 2.2 * math.sin(TAU * xs / 64)
            else:
                a = 6.5
                ph = TAU * 4 * xs / 64 + (math.pi if (xs // 16) % 2 else 0)
            pts.append((x, y0 + a * math.sin(ph)))
            mir.append((x, y0 - a * math.sin(ph)))
        if name == "AM":
            trace(f, mir, lambda x: mix(ramp(SYNTH, (x - 14) / 49), MAG, 0.45))
        trace(f, pts, lambda x: ramp(SYNTH, (x - 14) / 49))
    for i, (_, y0) in enumerate(rows):
        sparkle(f, 10, y0, dim(LVIOLET, 0.5 + 0.5 * pulse(t, n, 2, i / 3)), arm=1)
    return f


# ---------------------------------------------------------------- 04 transmitter beacon
BEA_T, BEA_D = "Transmitter beacon", ("Mountain-top transmitter with a blinking lamp sending ripples of signal "
                                      "out over the range toward the moon; town lights twinkle below")
_BEA16_STARS = stars(401, 12, 64, 16, ymax=7)
_BEA16_RIDGE = ridge_line(64, 41, 12, 5, peaks=7)
_BEA16_TOWN = [(x, 14 + (x * 7) % 2) for x in range(20, 64, 3)]


def arcs_out(f, cx, cy, t, n, rmax, spacing, a0=-1.05, a1=1.05, sy=1.0, gap=2.0, stops=(CYAN, BLUE, VIOLET, MAG)):
    k = 0
    while True:
        r = spacing * (k + t / n * 2)
        k += 1
        if r > rmax:
            break
        if r < 1.5:
            continue
        m = max(3, int((a1 - a0) * r / gap))
        c = ramp(list(stops), r / rmax)
        kk = 1.0 if r < rmax * 0.75 else 0.6
        for i in range(m + 1):
            a = a0 + (a1 - a0) * i / max(1, m)
            put(f, cx + r * math.cos(a), cy + r * math.sin(a) * sy, dim(c, kk))


@preset("p_sig_beacon", (64, 16), BEA_T, BEA_D, n=24, colors=24,
        note="tower + hut on the left peak, ripples spreading right, moon right, town lights")
def _(t, n):
    f = canvas()
    twinkle(f, _BEA16_STARS, t, n)
    d = ImageDraw.Draw(f)
    d.ellipse((57, 1, 61, 5), fill=(230, 235, 255))
    mountains(f, _BEA16_RIDGE, fill=MOUNT, rim=RIDGE)
    d.rectangle((0, 13, 63, 15), fill=BLACK)
    for i, (x, y) in enumerate(_BEA16_TOWN):
        put(f, x, y, dim(WARM if i % 3 else CYAN, 0.45 + 0.55 * pulse(t, n, 1 + i % 2, i * 0.37)))
    d.polygon([(0, 15), (4, 10), (18, 10), (24, 15)], fill=(26, 22, 84))
    arcs_out(f, 9, 3, t, n, rmax=34, spacing=6, a0=-0.9, a1=0.9, sy=0.7)
    tower(f, 9, 11, 8, (220, 210, 255), lamp=LAMP, lamp_k=0.35 + 0.65 * pulse(t, n, 2))
    d.rectangle((13, 8, 17, 10), fill=(60, 50, 140))
    put(f, 15, 9, WARM)
    return f


@preset("p_sig_beacon", (64, 64), BEA_T, BEA_D, n=24, colors=48, native=False,
        note="square: mast on the left summit, ripples cross the sky, lake with shimmering reflections")
def _(t, n):
    f = canvas(64, 64)
    twinkle(f, _BEA64_STARS, t, n)
    d = ImageDraw.Draw(f)
    d.ellipse((50, 5, 58, 13), fill=(230, 235, 255))
    d.point([(53, 8), (55, 11)], fill=ICE)
    mountains(f, _BEA64_BACK, fill=NAVY, rim=DVIOLET, ybottom=44)
    mountains(f, _BEA64_RIDGE, fill=MOUNT, rim=RIDGE, ybottom=44)
    d.polygon([(0, 44), (6, 30), (14, 26), (24, 30), (34, 44)], fill=(26, 22, 84))
    d.line([(6, 30), (14, 26), (24, 30)], fill=RIDGE)
    arcs_out(f, 15, 7, t, n, rmax=46, spacing=11, a0=-0.7, a1=0.9, sy=0.85, gap=1.6)
    tower(f, 15, 26, 18, (220, 210, 255), lamp=LAMP, lamp_k=0.35 + 0.65 * pulse(t, n, 2))
    d.rectangle((19, 22, 26, 27), fill=(60, 50, 140))
    d.rectangle((21, 24, 22, 25), fill=WARM)
    pines(f, 47, [(30, 7), (35, 9), (40, 6), (45, 8), (60, 7), (2, 6)])
    d.line((0, 47, 63, 47), fill=(40, 40, 150))
    for i, (x, y) in enumerate(_BEA64_TOWN):
        c = WARM if i % 3 else CYAN
        put(f, x, y, dim(c, 0.5 + 0.5 * pulse(t, n, 1, i * 0.29)))
        # reflection: a short dash in the lake that shimmers sideways
        dx = round(math.sin(TAU * (t / n * 2 + i * 0.37)))
        for j, yy in enumerate((52 + i % 3, 56 + i % 2)):
            put(f, x + (dx if j else 0), yy, dim(c, 0.6 - 0.15 * j))
    for y, x0, L in ((54, 4, 10), (58, 30, 14), (61, 12, 8), (60, 48, 9)):
        sh = round(2 * math.sin(TAU * (t / n + y / 13)))
        for x in range(x0 + sh, x0 + sh + L, 2):
            put(f, x, y, (34, 44, 150))
    return f


_BEA64_STARS = stars(402, 30, 64, 64, ymax=28)
_BEA64_BACK = ridge_line(64, 43, 38, 12, peaks=5)
_BEA64_RIDGE = ridge_line(64, 44, 44, 8, peaks=6)
_BEA64_TOWN = [(x, 48 + (x * 3) % 2) for x in range(26, 64, 4)]
for (_x, _y) in [(3, 48), (8, 49), (13, 48), (18, 49)]:
    _BEA64_TOWN.append((_x, _y))
_BEA64_TOWN = [(x, 45) if False else (x, y) for x, y in _BEA64_TOWN]
# town lights sit on the shore line (y 48/49); their reflections fall into the lake


# ---------------------------------------------------------------- 05 analog tuner
TUN_T, TUN_D = "Analog tuner", ("Radio front panel: tuning knob turning, dial needle sweeping the band over "
                                "a live oscilloscope trace, and a signal meter bouncing")
FRAME, FACE = (90, 60, 220), (16, 12, 56)


def knob(f, cx, cy, r, ang):
    d = ImageDraw.Draw(f)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(44, 34, 120), outline=LVIOLET)
    d.ellipse((cx - r + 2, cy - r + 2, cx + r - 2, cy + r - 2), outline=(80, 60, 180))
    ix, iy = cx + (r - 2) * math.cos(ang), cy + (r - 2) * math.sin(ang)
    d.line((cx + (r - 2) * 0.35 * math.cos(ang), cy + (r - 2) * 0.35 * math.sin(ang), ix, iy), fill=PINK)
    put(f, ix, iy, WHITE)


def vu(f, x0, y0, x1, y1, v):
    d = ImageDraw.Draw(f)
    d.rectangle((x0, y0, x1, y1), outline=FRAME)
    px, py = (x0 + x1) / 2, y1 - 1
    r = min((x1 - x0) / 2 - 1, (y1 - y0) - 2)
    for i in range(9):
        a = math.pi * (1.15 + 0.7 * i / 8)
        c = CYAN if i < 6 else MAG
        put(f, px + r * math.cos(a), py + r * math.sin(a), c)
    a = math.pi * (1.15 + 0.7 * v)
    d.line((px, py, px + (r - 1) * math.cos(a), py + (r - 1) * math.sin(a)), fill=WHITE)
    put(f, px, py, PINK)


@preset("p_sig_tuner", (64, 16), TUN_T, TUN_D, n=32, colors=16,
        note="knob left, dial scale + oscilloscope trace + sweeping needle centre, VU meter right")
def _(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    knob(f, 6, 7, 6, -math.pi / 2 + 1.1 * math.sin(TAU * t / n))
    d.rectangle((14, 0, 49, 15), outline=FRAME)
    for i, x in enumerate(range(16, 49, 2)):
        major = i % 4 == 0
        d.line((x, 1, x, 3 if major else 2), fill=ICE if major else DBLUE)
    for x in range(15, 49):
        y = 9.5 + 3.4 * math.sin(TAU * (3 * (x - 15) / 34 - 2 * t / n))
        put(f, x, y, ramp(SYNTH, (x - 15) / 33))
    nx = 31.5 + 15 * math.sin(TAU * t / n)
    d.line((nx, 1, nx, 14), fill=MAG)
    put(f, nx, 1, WHITE)
    v = 0.55 + 0.25 * math.sin(TAU * 2 * t / n) + 0.12 * math.sin(TAU * 5 * t / n + 1)
    vu(f, 51, 2, 63, 13, v)
    return f


@preset("p_sig_tuner", (64, 64), TUN_T, TUN_D, n=32, colors=40, native=False,
        note="square: FM dial 88-108 with needle over the scope trace on top, knob + meter + mode lamps below")
def _(t, n):
    f = canvas(64, 64)
    d = ImageDraw.Draw(f)
    d.rectangle((0, 0, 63, 27), outline=FRAME)
    labels = ["88", "94", "100", "106"]
    xs = [9, 24, 40, 55]
    for lab, x in zip(labels, xs):
        text35(f, x - width35(lab) // 2, 2, lab, ICE)
    for x in range(2, 62, 2):
        major = x in (8, 24, 40, 56)
        d.line((x, 9, x, 11 if major else 10), fill=ICE if major else DBLUE)
    for x in range(1, 63):
        y = 19 + 5 * math.sin(TAU * (4 * (x - 1) / 62 - 2 * t / n))
        put(f, x, y, ramp(SYNTH, x / 62))
        if x % 2:
            put(f, x, 19, (30, 30, 110))
    nx = 37 + 24 * math.sin(TAU * t / n)
    d.line((nx, 1, nx, 26), fill=MAG)
    put(f, nx, 1, WHITE)
    put(f, nx, 26, WHITE)
    knob(f, 14, 45, 12, -math.pi / 2 + 1.2 * math.sin(TAU * t / n))
    v = 0.55 + 0.25 * math.sin(TAU * 2 * t / n) + 0.12 * math.sin(TAU * 5 * t / n + 1)
    vu(f, 31, 31, 63, 52, v)
    text35(f, 33, 46, "S", LVIOLET)
    for i, (lab, on) in enumerate((("AM", 0), ("FM", 1), ("CW", 0))):
        x = 31 + i * 11
        put(f, x, 58, dim(MAG, 0.4 + 0.6 * pulse(t, n, 2)) if on else (50, 40, 120))
        put(f, x, 59, dim(MAG, 0.4 + 0.6 * pulse(t, n, 2)) if on else (50, 40, 120))
        text35(f, x + 2, 57, lab, ICE if on else (90, 80, 170))
    return f


# ===================================================================== 64x64 concepts
# ---------------------------------------------------------------- 06 satellite uplink
SAT_T, SAT_D = "Satellite uplink", ("Ground dish beaming data packets up to a satellite over the night side "
                                    "of the Earth; city lights glow along the blue limb")
_SAT64_STARS = stars(601, 34, 64, 64, ymax=44)
_SAT64_MW = [(x, y) for x, y in ((6, 4), (9, 7), (11, 9), (14, 12), (17, 14), (12, 6), (19, 17), (22, 19),
                                 (24, 22), (8, 9), (15, 9), (20, 13), (26, 24), (29, 27))]
_SAT64_CITY = None
EARTH = (80, 120, 82)


def _earth_limb(w, h, cx, cy, r):
    pts = []
    for x in range(w):
        dy = r * r - (x - cx) ** 2
        if dy >= 0:
            pts.append((x, cy - math.sqrt(dy)))
    return pts


@preset("p_sig_satuplink", (64, 64), SAT_T, SAT_D, n=24, colors=48,
        note="dish bottom-left, packet beam to the satellite top-right, Earth limb with city lights")
def _(t, n):
    f = canvas(64, 64)
    d = ImageDraw.Draw(f)
    for i, (x, y) in enumerate(_SAT64_MW):
        put(f, x, y, (70, 40, 150) if i % 3 else (120, 60, 200))
    twinkle(f, _SAT64_STARS, t, n)
    # Earth: limb arc and night side with city lights
    cx, cy, r = EARTH
    for y in range(36, 64):
        for x in range(64):
            dd = math.hypot(x - cx, y - cy)
            if dd <= r:
                put(f, x, y, (10, 14, 60) if dd < r - 2 else (dim(CYAN, 0.9) if dd > r - 1 else BLUE))
    for i, (x, y) in enumerate(_SAT64_CITY):
        put(f, x, y, dim(WARM if i % 4 else PINK, 0.45 + 0.55 * pulse(t, n, 1 + i % 2, i * 0.23)))
    # ground station
    pines(f, 63, [(1, 9), (5, 12), (25, 8), (29, 11), (33, 7)], fill=(22, 20, 84), rim=DVIOLET)
    d.rectangle((0, 55, 22, 63), fill=(28, 24, 78))
    d.line((0, 55, 22, 55), fill=DVIOLET)
    for wx in (3, 8, 13, 18):
        d.rectangle((wx, 58, wx + 1, 59), fill=WARM if wx != 13 else PINK)
    fx, fy = dish(f, 15, 41, 10, tilt=-0.62, mount_h=8)
    sx, sy = 47, 12
    for k, off in enumerate((-2, 0, 2)):
        c = (VIOLET, PINK, CYAN)[k]
        x0, y0, x1, y1 = fx + off * 0.6, fy + off * 0.8, sx - 4 + off * 0.4, sy + 3 + off * 0.5
        d.line((x0, y0, x1, y1), fill=dim(c, 0.55))
        dotted_line(f, x0, y0, x1, y1, None, step=6, phase=((t / n * 4) + k / 3) % 1, bright=WHITE if k == 1 else ICE,
                    every=1)
    g = 0.5 + 0.5 * pulse(t, n, 2)
    sparkle(f, fx, fy, dim(PINK, g), arm=2, core=WHITE)
    satellite(f, sx, sy, s=2, blink=dim(LAMP, 0.3 + 0.7 * pulse(t, n, 3)))
    return f


def _city_lights():
    r = random.Random(606)
    out = []
    cx, cy, rr = EARTH
    while len(out) < 26:
        x = r.randrange(26, 64)
        top = cy - math.sqrt(max(0, rr * rr - (x - cx) ** 2))
        if int(top) + 4 < 63:
            out.append((x, r.randrange(int(top) + 4, 64)))
    return out


_SAT64_CITY = _city_lights()


@preset("p_sig_satuplink", (64, 16), SAT_T, SAT_D, n=24, colors=32, native=False,
        note="strip: dish left, packet beam climbing to the satellite right, Earth limb along the bottom")
def _(t, n):
    f = canvas()
    twinkle(f, _SAT16_STARS, t, n)
    d = ImageDraw.Draw(f)
    for x in range(14, 64):
        y = 15 - int(2.5 * math.sin(math.pi * (x - 14) / 60))
        put(f, x, y, CYAN)
        for yy in range(y + 1, 16):
            put(f, x, yy, (10, 14, 60))
    for i, x in enumerate(range(20, 62, 5)):
        put(f, x, 15, dim(WARM, 0.45 + 0.55 * pulse(t, n, 1, i * 0.3)))
    d.rectangle((0, 13, 9, 15), fill=(28, 24, 78))
    put(f, 3, 14, WARM)
    put(f, 7, 14, PINK)
    fx, fy = dish(f, 6, 7, 5, tilt=-0.5, mount_h=4)
    sx, sy = 54, 4
    for k, off in enumerate((-1, 1)):
        d.line((fx, fy + off, sx - 7, sy + off * 0.5), fill=dim((VIOLET, CYAN)[k], 0.6))
        dotted_line(f, fx, fy + off, sx - 7, sy + off * 0.5, None, step=8, phase=((t / n * 4) + k / 2) % 1,
                    bright=WHITE, every=1)
    sparkle(f, fx, fy, dim(PINK, 0.5 + 0.5 * pulse(t, n, 2)), arm=1)
    satellite(f, sx, sy, s=1, blink=dim(LAMP, 0.3 + 0.7 * pulse(t, n, 3)))
    return f


_SAT16_STARS = stars(602, 14, 64, 16, ymax=12)


# ---------------------------------------------------------------- 07 digital sync grid
SYN_T, SYN_D = "Digital sync grid", ("Sync rings pulse out of a star above a lone tower while a neon "
                                     "perspective grid rolls toward you")
_SYN64_STARS = stars(701, 28, 64, 64, ymax=34)
_SYN64_L = [min(39, int(26 + abs(x - 10) * 0.9 + (2 if x in (15, 16, 17) else 0))) for x in range(26)]
_SYN64_R = [min(39, int(26 + abs(x - 15) * 0.9 + (2 if x in (6, 7, 8) else 0))) for x in range(26)]


def grid_floor(f, hy, t, n, x_c=32, spread=10, rows=6, z_far=6.0, cols=(BLUE, VIOLET), node=CYAN):
    """Perspective floor below horizon hy; lines roll toward the viewer once per loop (integer)."""
    H = f.height
    depth = H - 1 - hy
    d = ImageDraw.Draw(f)
    # radiating lines
    for i in range(-6, 7):
        xb = x_c + i * spread
        c = cols[1] if i == 0 else cols[0]
        steps = depth * 2
        for s in range(steps // 4 if i else 0, steps + 1):
            y = hy + depth * s / steps
            x = x_c + (xb - x_c) * s / steps
            put(f, x, y, dim(c, 0.45 + 0.55 * s / steps))
    # horizontal lines: z = 1 + k - phase; y = hy + depth / z
    for k in range(rows + 1):
        z = 0.5 + 1.5 * (k + 1 - t / n)
        y = hy + depth / z
        if y > H - 1 + 0.49:
            continue
        kk = min(1.0, 1.4 / z + 0.15)
        c = dim(mix(cols[0], CYAN, 0.3), kk)
        if z < 4.5:
            for x in range(0, f.width, 1 if z < 2.5 else 2):
                put(f, x, y, c)
        for i in range(-6, 7):
            xb = x_c + i * spread
            x = x_c + (xb - x_c) * (y - hy) / depth
            if z < 3.0:
                put(f, x, y, dim(node, kk))
    d.line((0, hy, f.width - 1, hy), fill=CYAN)


@preset("p_sig_syncgrid", (64, 64), SYN_T, SYN_D, n=24, colors=40,
        note="star + expanding dotted sync rings, tower on the horizon, mountains, rolling perspective grid")
def _(t, n):
    f = canvas(64, 64)
    twinkle(f, _SYN64_STARS, t, n)
    cx, cy = 32, 15
    for k in range(4):
        r = 3 + 4 * (k + t / n)
        if r < 18.5:
            c = mix(WHITE, MAG, (r - 3) / 15)
            dotted_ring(f, cx, cy, r, c if r < 15 else dim(c, 0.6), gap=2.0)
    for y in range(0, 38, 2):
        if abs(y - cy) > 3:
            put(f, cx, y, dim(VIOLET, 0.8))
    for x in range(10, 55, 2):
        if abs(x - cx) > 3:
            put(f, x, cy, dim(VIOLET, 0.6))
    sparkle(f, cx, cy, LVIOLET, arm=3)
    d = ImageDraw.Draw(f)
    mountains(f, _SYN64_L, fill=MOUNT, rim=MAG, ybottom=39)
    prof_r = [None] * 38 + _SYN64_R
    for x in range(38, 64):
        y = prof_r[x]
        d.line((x, y, x, 39), fill=MOUNT)
        put(f, x, y, MAG)
    pines(f, 39, [(2, 4), (6, 3), (12, 4), (52, 4), (57, 3), (61, 4)])
    # tower on the horizon
    d.polygon([(28, 39), (31, 27), (33, 27), (36, 39)], fill=(36, 26, 110))
    tower(f, 32, 39, 14, LVIOLET, lamp=WHITE, lamp_k=0.5 + 0.5 * pulse(t, n, 2))
    grid_floor(f, 40, t, n, spread=16, rows=4)
    return f


@preset("p_sig_syncgrid", (64, 16), SYN_T, SYN_D, n=24, colors=24, native=False,
        note="strip: small ring burst over the tower on the left half of the sky, grid floor rolls below")
def _(t, n):
    f = canvas()
    cx, cy = 32, 3
    for k in range(3):
        r = 1.5 + 2.2 * (k + t / n)
        if r < 7.5:
            dotted_ring(f, cx, cy, r, mix(WHITE, MAG, (r - 1.5) / 6), gap=2.0, sy=0.8)
    sparkle(f, cx, cy, LVIOLET, arm=1)
    for x, h in ((4, 2), (10, 3), (16, 2), (48, 2), (54, 3), (60, 2)):
        for dx in range(-h, h + 1):
            put(f, x + dx, 8 - (h - abs(dx)), MOUNT if abs(dx) < h else MAG)
    tower(f, 32, 8, 4, LVIOLET)
    d = ImageDraw.Draw(f)
    for i in range(-4, 5):                          # floor rays from the vanishing point
        d.line((32 + i * 2, 9, 32 + i * 14, 15), fill=VIOLET if i == 0 else DBLUE)
    for k in range(2):                              # two cross lines rolling toward the viewer
        z = 0.6 + 1.6 * (k + 1 - t / n)
        y = 8 + 7 / z
        if y <= 15.4:
            d.line((0, round(y), 63, round(y)), fill=dim(mix(BLUE, CYAN, 0.4), min(1.0, 1.5 / z + 0.2)))
    d.line((0, 8, 63, 8), fill=CYAN)
    return f


# ---------------------------------------------------------------- 08 frequency hopping
HOP_T, HOP_D = "Frequency hopping", ("Neon equalizer bars with a signal hopping between channel peaks along "
                                     "dotted arcs, mirrored in a rippling reflection")
_HOP64_STARS = stars(801, 22, 64, 64, ymax=18)


def hop_scene(f, base_y, max_h, bar_w, gap, t, n, arcs, refl=True, seg=True, ring_r=2):
    count = (f.width + gap) // (bar_w + gap)
    vals = bars_values(count, t, n, 808, [(0.06, 0.45, 0.07), (0.35, 0.6, 0.08), (0.68, 0.65, 0.08),
                                          (0.95, 0.5, 0.07), (0.52, 0.25, 0.1)], lo=0.12, amp=0.16)
    tops = []
    for i, v in enumerate(vals):
        x0 = i * (bar_w + gap)
        hgt = max(1, round(v * max_h))
        c = ramp([CYAN, BLUE, VIOLET, MAG, CYAN], i / max(1, count - 1))
        for k in range(hgt):
            y = base_y - k
            if seg and k % 3 == 2:
                continue
            cc = mix(c, WHITE, 0.55) if k == hgt - 1 else c
            for b in range(bar_w):
                put(f, x0 + b, y, cc)
                if refl:
                    ry = base_y + 2 + k
                    if ry < f.height and k % 2 == 0:
                        wob = round(math.sin(TAU * (t / n * 2 + ry / 6)))
                        put(f, x0 + b + wob, ry, dim(c, 0.5 if k < 6 else 0.35))
        tops.append((x0 + bar_w / 2 - 0.5, base_y - hgt + 1))
    # hops: dotted arcs between hop points, a bright token rides each arc once per loop
    for j, (xa, xb, top) in enumerate(arcs):
        ya = yb = base_y - max_h - 1
        pts = []
        for s in range(0, 41):
            u = s / 40
            pts.append((xa + (xb - xa) * u, ya - top * 4 * u * (1 - u)))
        col = BLUE if j % 2 == 0 else MAG
        for s in range(0, 41, 2):
            put(f, *pts[s], dim(col, 0.85))
        ph = (t / n + j / len(arcs)) % 1
        k = math.sin(math.pi * ph)
        x, y = pts[min(40, int(ph * 40))]
        sparkle(f, x, y, dim(WHITE, k), arm=1, core=dim(WHITE, k))
        for xx in (xa, xb):                          # dotted stem from the marker down to its bar
            bi = min(len(tops) - 1, max(0, round((xx - bar_w / 2 + 0.5) / (bar_w + gap))))
            for yy in range(int(ya) + ring_r + 1, int(tops[bi][1]), 2):
                put(f, xx, yy, dim(col, 0.7))
        for (xx, yy), land in (((xa, ya), ph < 0.12), ((xb, yb), ph > 0.88)):
            c = WHITE if land else ICE
            if ring_r >= 2:
                d = ImageDraw.Draw(f)
                d.ellipse((xx - ring_r, yy - ring_r, xx + ring_r, yy + ring_r), outline=c)
                put(f, xx, yy, PINK if land else BLACK)
            else:
                put(f, xx, yy, c)
    return tops


@preset("p_sig_hophop", (64, 64), HOP_T, HOP_D, n=24, colors=48,
        note="segmented bars, three dotted hop arcs with ring markers, water reflection below")
def _(t, n):
    f = canvas(64, 64)
    twinkle(f, _HOP64_STARS, t, n)
    for x in range(64):
        put(f, x, 46, (40, 40, 160) if x % 2 else (20, 20, 90))
    hop_scene(f, 45, 27, 2, 1, t, n, arcs=[(4, 22, 9), (22, 43, 12), (43, 61, 9)], refl=True)
    return f


@preset("p_sig_hophop", (64, 16), HOP_T, HOP_D, n=24, colors=32, native=False,
        note="strip: bars along the bottom, small hop arcs riding over them")
def _(t, n):
    f = canvas()
    hop_scene(f, 15, 8, 2, 1, t, n, arcs=[(3, 21, 3), (21, 42, 4), (42, 60, 3)], refl=False, seg=False, ring_r=1)
    return f


# ---------------------------------------------------------------- 09 spectrum analyzer
SPE_T, SPE_D = "Spectrum analyzer", ("FFT spectrum of the 2.4 GHz band: three channel humps breathing over the noise "
                                     "floor, dBm scale and graticule, in Midnight Synth colours")
SPEC_STOPS = [DBLUE, BLUE, CYAN, VIOLET, MAG, PINK]


@preset("p_sig_spectrum", (64, 64), SPE_T, SPE_D, n=24, colors=40,
        note="header 'RF -56DBM', dBm scale left, 2.3-2.6G axis, graticule, gradient bars + peak caps")
def _(t, n):
    f = canvas(64, 64)
    d = ImageDraw.Draw(f)
    text35(f, 1, 1, "RF", CYAN)
    lvl = -56 + (0 if pulse(t, n, 1) < 0.5 else 0)
    text35(f, 11, 1, f"{lvl}DBM", ICE)
    text35(f, 47, 1, "FFT", LVIOLET)
    d.line((0, 7, 63, 7), fill=FRAME)
    x0, x1, y0, y1 = 14, 63, 9, 55
    for i, lab in enumerate(("-20", "-60", "-100")):
        y = y0 + 4 + i * 18
        text35(f, 13 - width35(lab), y - 2, lab, LVIOLET)
        for x in range(x0, x1 + 1, 2):
            put(f, x, y, (26, 26, 100))
    for x in range(x0, x1 + 1, 12):
        for y in range(y0 + 1, y1, 2):
            put(f, x, y, (26, 26, 100))
    w = x1 - x0 + 1
    vals = bars_values(w, t, n, 909, [(0.22, 0.7, 0.05), (0.18, 0.35, 0.12), (0.66, 0.5, 0.06), (0.86, 0.3, 0.05)],
                       lo=0.1, amp=0.12)
    hmax = y1 - y0 - 1
    for i, v in enumerate(vals):
        h = max(1, round(v * hmax))
        x = x0 + i
        for k in range(h):
            if k % 2 == 1 and k < h - 2:
                continue
            put(f, x, y1 - k, ramp(SPEC_STOPS, k / hmax))
        put(f, x, y1 - h, WHITE if v > 0.5 else ICE)
    d.line((x0, y1 + 1, x1, y1 + 1), fill=FRAME)
    for lab, x in (("2.3", 14), ("2.4", 30), ("2.5", 46)):
        text35(f, x - 1, 58, lab, ICE if lab != "2.4" else CYAN)
    text35(f, 60, 58, "G", LVIOLET)
    # marker on the strongest hump
    mx = x0 + int(0.22 * (w - 1))
    put(f, mx, y0 + 1, dim(MAG, 0.5 + 0.5 * pulse(t, n, 2)))
    put(f, mx - 1, y0, dim(MAG, 0.5 + 0.5 * pulse(t, n, 2)))
    put(f, mx + 1, y0, dim(MAG, 0.5 + 0.5 * pulse(t, n, 2)))
    return f


@preset("p_sig_spectrum", (64, 16), SPE_T, SPE_D, n=24, colors=24, native=False,
        note="strip: full-width spectrum with gradient bars, white peak caps, faint graticule")
def _(t, n):
    f = canvas()
    for x in range(0, 64, 2):
        put(f, x, 5, (26, 26, 100))
    vals = bars_values(64, t, n, 909, [(0.22, 0.7, 0.05), (0.18, 0.35, 0.12), (0.66, 0.5, 0.06), (0.86, 0.3, 0.05)],
                       lo=0.1, amp=0.12)
    for x, v in enumerate(vals):
        h = max(1, round(v * 15))
        for k in range(h):
            put(f, x, 15 - k, ramp(SPEC_STOPS, k / 15))
        put(f, x, 15 - h, WHITE if v > 0.5 else ICE)
    text35(f, 1, 0, "RF", CYAN)
    return f


# ---------------------------------------------------------------- 10 APRS decode
APR_T, APR_D = "APRS decode", ("Packet decode panel: example APRS position report (placeholder callsign N0CALL) "
                               "with packets hopping between stations over the globe and a satellite relay")
APRS_LINES = [("CALL", "N0CALL-9"), ("TYPE", "POSITION"), ("LAT", "45.0000N"), ("LON", "120.000W"),
              ("MSG", "TESTING 123")]
APRS_NODES64 = [(6, 61), (20, 55), (36, 58), (52, 52)]


def _globe(f, cx, cy, r, y_top, land_seed=5):
    rnd = random.Random(land_seed)
    blobs = [(rnd.uniform(0, 64), rnd.uniform(y_top, 64), rnd.uniform(4, 9)) for _ in range(6)]
    for y in range(y_top, f.height):
        for x in range(f.width):
            dd = math.hypot(x - cx, y - cy)
            if dd > r:
                continue
            land = any(math.hypot(x - bx, (y - by) * 1.4) < br for bx, by, br in blobs)
            if dd > r - 1:
                c = CYAN
            elif land:
                c = (60, 50, 190) if (x + y) % 2 else (40, 34, 150)
            else:
                c = (12, 16, 70) if (x % 4 and y % 3) else (26, 34, 120)
            put(f, x, y, c)


@preset("p_sig_aprs", (64, 64), APR_T, APR_D, n=24, colors=40,
        note="'DECODE>APRS' header, 6 example fields in the 3x5 font, globe with packet path + satellite")
def _(t, n):
    f = canvas(64, 64)
    d = ImageDraw.Draw(f)
    d.rectangle((0, 0, 46, 6), fill=(30, 22, 110))
    txt(f, 1, 1, "DECODE", CYAN)
    txt(f, 26, 1, ">", MAG)
    txt(f, 30, 1, "APRS", WHITE)
    for i, (k, v) in enumerate(APRS_LINES):
        y = 9 + i * 6
        txt(f, 0, y, k, LVIOLET)
        txt(f, 16 if len(k) <= 3 else 17, y, v, ICE if i else WHITE)
    # blinking cursor after the message
    if pulse(t, n, 2) > 0.5:
        cx = 17 + txt_w(APRS_LINES[-1][1]) + 2
        if cx < 63:
            d.rectangle((cx, 33, cx + 1, 37), fill=CYAN)
    _globe(f, 32, 118, 72, 45)
    # path between stations: dotted, packet travels once per loop and fades at the ends
    segs = list(zip(APRS_NODES64, APRS_NODES64[1:]))
    for a, b in segs:
        dotted_line(f, *a, *b, dim(MAG, 0.8), step=2)
    ph = t / n
    seg_i = min(len(segs) - 1, int(ph * len(segs)))
    u = ph * len(segs) - seg_i
    (ax, ay), (bx, by) = segs[seg_i]
    k = math.sin(math.pi * ph) ** 0.5
    sparkle(f, ax + (bx - ax) * u, ay + (by - ay) * u, dim(WHITE, k), arm=1, core=dim(WHITE, k))
    for i, (x, y) in enumerate(APRS_NODES64):
        g = 0.55 + 0.45 * pulse(t, n, 1, i / len(APRS_NODES64))
        d.rectangle((x - 1, y - 1, x + 1, y + 1), fill=dim(PINK, g))
        put(f, x, y, WHITE)
    # satellite relay over the last node
    satellite(f, 55, 41, s=1, blink=dim(LAMP, 0.3 + 0.7 * pulse(t, n, 3)))
    dotted_line(f, 55, 44, 52, 50, dim(CYAN, 0.9), step=2, phase=(t / n * 4) % 1)
    return f


APRS_ROLL = ["N0CALL-9", "POSITION", "45.0000N", "120.000W", "64KT 312", "TESTING 123"]
APRS_ROLL_COLS = [CYAN, LVIOLET, ICE, ICE, ICE, PINK]


@preset("p_sig_aprs", (64, 16), APR_T, APR_D, n=6 * len(APRS_ROLL), ms=110, colors=16, native=False,
        note="strip: 'APRS' tag left, decoded fields roll upward line by line, globe edge with packet path below")
def _(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rectangle((0, 1, 16, 9), fill=(30, 22, 110))
    txt(f, 1, 3, "APRS", WHITE)
    put(f, 18, 5, dim(MAG, 0.4 + 0.6 * pulse(t, n, len(APRS_ROLL))))
    # rolling decode lines: 6 px pitch, 1 px a frame, one full cycle per loop
    win = Image.new("RGB", (44, 6 * len(APRS_ROLL) + 6))
    for i, s in enumerate(APRS_ROLL + APRS_ROLL[:1]):
        txt(win, 0, i * 6, s, (APRS_ROLL_COLS + APRS_ROLL_COLS[:1])[i])
    view = win.crop((0, t, 44, t + 11))
    mask = Image.new("L", view.size, 255)
    f.paste(view, (20, 0), mask)
    for x in range(64):
        y = 14 - round(1.6 * math.sin(math.pi * x / 63))
        put(f, x, y, CYAN)
        for yy in range(y + 1, 16):
            put(f, x, yy, (30, 30, 130) if (x + yy) % 3 == 0 else (12, 16, 70))
    nodes = [(5, 14), (22, 13), (42, 13), (59, 14)]
    ph = t / n
    i = min(2, int(ph * 3))
    u = ph * 3 - i
    (ax, ay), (bx, by) = nodes[i], nodes[i + 1]
    k = math.sin(math.pi * ph) ** 0.5
    for j, (x, y) in enumerate(nodes):
        put(f, x, y - 1, dim(PINK, 0.55 + 0.45 * pulse(t, n, 1, j / 4)))
    put(f, ax + (bx - ax) * u, ay - 1 + (by - ay) * u - 1, dim(WHITE, k))
    return f


# ===================================================================== build
def frames_of(entry):
    """Render all frames, then share one small palette (BLE budget: fewer colours -> shorter LZW codes)."""
    frames = [entry["fn"](t, entry["n"]).convert("RGB") for t in range(entry["n"])]
    w, h = entry["size"]
    sheet = Image.new("RGB", (w, h * len(frames)))
    for i, fr in enumerate(frames):
        sheet.paste(fr, (0, i * h))
    pal = sheet.quantize(colors=entry["colors"], method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    return [fr.quantize(palette=pal, dither=Image.Dither.NONE).convert("RGB") for fr in frames]


def by_name():
    out = {}
    for e in REGISTRY:
        out.setdefault(e["name"], {})[e["size"]] = e
    return out


def build(entry) -> dict:
    name, size = entry["name"], entry["size"]
    native = [e for e in REGISTRY if e["name"] == name and e["native"]][0]
    frames = frames_of(entry)
    gif = encode_gif(frames, entry["ms"])
    m = loop_metric_frames(frames)
    rep = max(frames, key=lambda f: sum(f.convert("L").tobytes()))
    common = {"family": FAMILY, "title": native["title"], "description": native["desc"], "live_data": False,
              "native_size": "%dx%d" % native["size"], "concept": "Midnight Synth RF / Signal board (2026-10-14)"}
    if size == (64, 16):
        out = OUT16
        (out / f"{name}.gif").write_bytes(gif)
        rep.save(out / f"{name}.png")
        rep.resize((640, 160), Image.Resampling.NEAREST).save(out / f"{name}_preview_10x.png")
        meta = {"name": name, "kind": "gif", "file": f"{name}.gif", "still": f"{name}.png",
                "preview": f"{name}_preview_10x.png", **common, "frames": entry["n"], "frame_ms": entry["ms"],
                "bytes": len(gif), "loop": "seamless (tools/loopcheck.py)" if m["ok"] else "JUMP",
                "layout16": entry["note"], "generator": f"tools/make_rfsig_presets.py::{name}@64x16"}
    else:
        out = OUT64
        (out / f"{name}.gif").write_bytes(gif)
        rep.resize((384, 384), Image.Resampling.NEAREST).save(out / f"{name}_preview_6x.png")
        meta = {"name": name, "kind": "gif", "file": f"{name}.gif", "preview": f"{name}_preview_6x.png",
                **common, "size": "64x64", "frames": entry["n"], "frame_ms": entry["ms"], "bytes": len(gif),
                "loop": "seamless" if m["ok"] else "JUMP", "layout64": entry["note"],
                "generator": f"tools/make_rfsig_presets.py::{name}@64x64"}
    (out / f"{name}.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return {**meta, "_size": size, "_native": entry["native"], "_frames": frames, "_rep": rep, "_loop": m}


def main(argv):
    sheet = preview = None
    if "--sheet" in argv:
        sheet = argv[argv.index("--sheet") + 1]
    if "--preview" in argv:
        preview = argv[argv.index("--preview") + 1]
    names = [a for a in argv if a.startswith("p_sig_")]
    results = []
    for e in REGISTRY:
        if names and e["name"] not in names:
            continue
        r = build(e)
        results.append(r)
        m = r["_loop"]
        print(f"{r['name']:18} {r['_size'][0]}x{r['_size'][1]:<3} {'native' if r['_native'] else 'companion':9} "
              f"{r['frames']:3}f x{r['frame_ms']}ms {r['bytes']:6} B  seam {m['seam']:5} p90 {m['p90']:5} "
              f"mc {m['seam_mc']:5}/{m['p90_mc']:5} {'seamless' if m['ok'] else 'JUMP'}")
    if sheet or preview:
        import rfsig_sheet
        if sheet:
            print("sheet", rfsig_sheet.contact_sheet(results, sheet))
        if preview:
            print("preview", rfsig_sheet.preview_gif(results, preview))
    print(len(results), "builds")
    return 0 if all(r["_loop"]["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
