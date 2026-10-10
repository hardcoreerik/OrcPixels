"""50 tailored OrcPixels presets (p_<family>_<name>).

    python tools/make_personal_presets.py            # build all 48 + contact sheets
    python tools/make_personal_presets.py p_rf_iq    # build selected

Families: proj (his real projects), rf (SDR/RF visuals), orc (warchief/TheOrc
lore), code (AI/coding moods), hw (ESP32/hardware), amb (ambient loops).
Every animated preset is a seamless loop: all motion is a function of the loop
phase t/N with integer cycles, scrolls wrap by exactly one period, and
waterfalls use a wrapping history (see tools/loopcheck.py). Art only: no
preset claims live data.
"""
from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from make_styles import LoopWaterfall, WF_MAP, ICE_MAP  # noqa: E402
from make_wave_small import orc_icon, wordmark  # noqa: E402
from orcpixels.render import _lerp_stops, encode_gif  # noqa: E402
from pixfont import text35, width35  # noqa: E402

OUT = ROOT / "presets"
W, H = 64, 16
BLACK = (0, 0, 0)
LIME, LIME2, DLIME = (140, 252, 40), (80, 255, 40), (30, 110, 10)
CYAN, DCYAN, WHITE = (90, 220, 255), (0, 70, 90), (225, 255, 255)
AMBER, RED, MAG = (255, 170, 20), (255, 50, 40), (255, 60, 200)
GRAD = [WHITE, CYAN, (110, 250, 140), (190, 252, 50)]
TAU = 2 * math.pi
UNI = ImageFont.truetype(str(ROOT / "orcpixels/fonts/unifont.otf"), 16)

REGISTRY: list[dict] = []


def preset(name, family, title, desc, why, n=None, ms=100):
    def deco(fn):
        REGISTRY.append(dict(name=name, family=family, title=title, desc=desc, why=why, n=n, ms=ms, fn=fn))
        return fn
    return deco


def canvas(bg=BLACK):
    return Image.new("RGB", (W, H), bg)


def grad(x0, w, stops=GRAD):
    return lambda gx, gy: _lerp_stops(stops, (gx - x0) / max(1, w - 1))


def dim(c, k):
    return tuple(max(0, min(255, int(v * k))) for v in c)


def uni(img, x, y, s, color):
    m = Image.new("L", (W, H))
    ImageDraw.Draw(m).text((x, y), s, fill=255, font=UNI)
    m = m.point(lambda p: 255 if p > 128 else 0)
    if callable(color):
        col = Image.new("RGB", (W, H))
        px = col.load()
        for gx in range(W):
            for gy in range(H):
                px[gx, gy] = color(gx, gy)
        img.paste(col, (0, 0), m)
    else:
        img.paste(Image.new("RGB", (W, H), color), (0, 0), m)


def wrap_scroll(strip: Image.Image, off: int, x=0, y=0, w=W, into=None):
    """Paste a horizontally wrapping (tiled) window of `strip` at offset off."""
    into = into or canvas()
    sw = strip.width
    off %= sw
    reps = (off + w) // sw + 1
    tiled = Image.new("RGB", (sw * reps, strip.height))
    for i in range(reps):
        tiled.paste(strip, (i * sw, 0))
    into.paste(tiled.crop((off, 0, off + w, strip.height)), (x, y))
    return into


def ring(d, cx, cy, r, color):
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=color)


# =============================================================== proj
@preset("p_proj_theorc", "proj", "TheOrc", "Orc icon + 'TheOrc' with a tusk-glint highlight sweeping the name",
        "", n=24, ms=90)
def _(t, n):
    f = canvas(); f.paste(orc_icon(), (0, 0))
    hx = 16 + (t / n) * 80 - 8                     # highlight band travels 16..96 (wraps off-screen)
    uni(f, 16, 0, "TheOrc", lambda gx, gy: WHITE if abs(gx - hx - (gy - 8) * 0.5) < 2.5 else
        _lerp_stops([LIME, (120, 250, 150), CYAN], (gx - 16) / 47))
    return f


@preset("p_proj_orcmesh", "proj", "OrcMesh", "Five mesh nodes with packets hopping along the links + 'ORCMESH'",
        "", n=20, ms=100)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    nodes = [(3, 12), (10, 3), (18, 11), (26, 4), (31, 13)]
    edges = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 2), (2, 4)]
    for a, b in edges:
        d.line(nodes[a] + nodes[b], fill=(0, 60, 25))
    for i, (a, b) in enumerate(edges):
        p = (t / n * 2 + i / len(edges)) % 1.0              # 2 hops per loop per edge
        x = nodes[a][0] + (nodes[b][0] - nodes[a][0]) * p
        y = nodes[a][1] + (nodes[b][1] - nodes[a][1]) * p
        d.point((round(x), round(y)), fill=CYAN)
    for x, y in nodes:
        d.rectangle((x - 1, y - 1, x + 1, y + 1), fill=LIME)
    text35(f, 35, 2, "ORC", LIME); text35(f, 35, 9, "MESH", CYAN)
    return f


@preset("p_proj_orclink", "proj", "OrcLink", "Chain links with a data pulse running through + 'ORCLINK / RUST EDGE'",
        "", n=16, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    for i in range(4):
        x = 1 + i * 7
        d.rounded_rectangle((x, 5 + (i % 2) * 2, x + 8, 9 + (i % 2) * 2), radius=2, outline=dim(LIME, 0.6))
    px = (t / n) * 34 - 2
    for k in range(3):
        xx = int(px) - k
        if 0 <= xx < 32:
            for y in range(4, 13):
                if f.getpixel((xx, y)) != BLACK:
                    f.putpixel((xx, y), WHITE if k == 0 else CYAN)
    text35(f, 35, 2, "ORCLINK", grad(35, 27))
    text35(f, 35, 9, "RUST", AMBER)
    return f


@preset("p_proj_orcmaps", "proj", "OrcMaps", "Contour-line terrain scrolling under a bouncing map pin + 'ORCMAPS'",
        "", n=32, ms=90)
def _(t, n):
    f = canvas(); px = f.load()
    for x in range(W):
        u = (x + 2 * t) % 64 / 64                          # scroll 64 px per loop
        for y in range(H):
            h = 0.5 * math.sin(TAU * (u * 2) + y * 0.35) + 0.5 * math.sin(TAU * u * 3 + 1.1) * math.cos(y * 0.4)
            if abs((h * 4) % 1.0 - 0.5) < 0.09:
                px[x, y] = (0, 80, 40)
    d = ImageDraw.Draw(f)
    d.rectangle((0, 0, 31, 6), fill=BLACK)
    text35(f, 1, 1, "ORCMAPS", grad(1, 27))
    by = 8 - round(2 * abs(math.sin(TAU * t / n * 2)))
    d.ellipse((46, by, 50, by + 4), fill=RED); d.point((48, by + 6), fill=RED); d.point((48, by + 5), fill=RED)
    d.point((48, by + 2), fill=WHITE)
    return f


@preset("p_proj_esprtlsdr", "proj", "esp-rtl-sdr", "USB packets flowing from an RTL dongle into an ESP32 + 'ESP-RTL-SDR'",
        "", n=16, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.rectangle((1, 9, 12, 14), outline=CYAN); text35(f, 3, 10, "", CYAN)
    d.line((13, 11, 14, 11), fill=CYAN); d.line((13, 12, 14, 12), fill=CYAN)
    d.rectangle((49, 8, 62, 15), outline=LIME)
    for i in range(3):
        d.point((51 + i * 4, 15), fill=LIME)
    for k in range(4):
        x = 15 + ((t / n + k / 4) % 1.0) * 33
        d.rectangle((int(x), 11, int(x) + 1, 12), fill=WHITE if k % 2 else AMBER)
    d.line((15, 10, 48, 10), fill=(0, 45, 20)); d.line((15, 13, 48, 13), fill=(0, 45, 20))
    text35(f, 1, 1, "ESP-RTL-SDR", grad(1, 43))
    text35(f, 50, 10, "P4", LIME)
    return f


@preset("p_proj_neondrive", "proj", "NEONDRIVE", "Synthwave grid road rushing toward a magenta horizon + 'NEONDRIVE'",
        "", n=16, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    hy = 7
    d.line((0, hy, 63, hy), fill=MAG)
    for i in range(-6, 7):
        d.line((32 + i * 3, hy, 32 + i * 14, 15), fill=(120, 0, 110))
    for k in range(4):
        p = ((t / n) + k / 4) % 1.0
        y = hy + 1 + (p * p) * 8
        d.line((0, round(y), 63, round(y)), fill=dim(MAG, 0.35 + 0.65 * p))
    text35(f, 14, 1, "NEONDRIVE", lambda gx, gy: _lerp_stops([CYAN, MAG], (gx - 14) / 34))
    return f


@preset("p_proj_visualizer", "proj", "TheVisualizer", "Retro audio bars bouncing in sync + 'VISUALIZER'",
        "", n=24, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    for i in range(16):
        a = TAU * t / n
        v = 0.5 + 0.25 * math.sin(a * (1 + i % 3) + i * 0.7) + 0.2 * math.sin(2 * a + i * 1.3)
        h = max(1, round(v * 9))
        for y in range(h):
            d.line((i * 4, 15 - y, i * 4 + 2, 15 - y), fill=_lerp_stops([LIME, CYAN, MAG], y / 8))
    d.rectangle((0, 0, 63, 5), fill=BLACK)
    text35(f, 12, 0, "VISUALIZER", grad(12, 39, [MAG, CYAN, LIME]))
    return f


@preset("p_proj_beamforge", "proj", "BeamForge", "DMX laser fan sweeping from a projector + 'BEAMFORGE'",
        "", n=24, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    ox, oy = 2, 14
    for k in range(5):
        ang = -0.15 - 0.55 * (0.5 + 0.5 * math.sin(TAU * t / n + k * 0.5))
        x2 = ox + 70 * math.cos(ang); y2 = oy + 70 * math.sin(ang)
        d.line((ox, oy, x2, y2), fill=[LIME, CYAN, MAG, LIME, CYAN][k])
    d.rectangle((0, 13, 4, 15), fill=(60, 60, 60))
    d.rectangle((24, 9, 61, 15), fill=BLACK)
    text35(f, 25, 10, "BEAMFORGE", grad(25, 35, [MAG, CYAN, LIME]))
    return f


# ================================================================= rf
def am_spectrum(t, n, x0, w, h, y0, f, burst_phase=0.0):
    d = ImageDraw.Draw(f)
    cx = x0 + w // 2
    env = max(0.0, math.sin(TAU * (t / n) * 2 + burst_phase))   # voice bursts, 2 per loop
    for x in range(x0, x0 + w, 1):
        dx = abs(x - cx)
        v = 0.08 + (1.0 if dx == 0 else 0) + env * 0.7 * math.exp(-dx / 3.5) * (0.7 + 0.3 * math.cos(dx * 1.7 + TAU * t / n * 4))
        hh = max(1, min(h, round(v * h)))
        d.line((x, y0 + h - 1, x, y0 + h - hh), fill=_lerp_stops([DLIME, LIME, CYAN], min(1, v)))


@preset("p_rf_airband", "rf", "Airband AM", "Plane crossing over an AM carrier whose sidebands swell with voice bursts",
        "", n=16, ms=100)
def _(t, n):
    f = canvas()
    am_spectrum(t, n, 26, 37, 12, 4, f)
    text35(f, 1, 1, "AIRBAND", grad(1, 27))
    text35(f, 1, 9, "AM", dim(CYAN, 0.8))
    d = ImageDraw.Draw(f)
    x = (t * 4) % 64                                             # plane wraps 64 px per loop
    for dx, dy in [(0, 0), (1, 0), (2, 0), (3, 0), (1, -1), (1, 1), (3, -1)]:
        d.point(((x + dx) % 64, 7 + dy), fill=WHITE)
    return f


@preset("p_rf_p25", "rf", "P25 trunking", "'P25' + control channel and voice channels lighting in a trunking rotation, C4FM dibit strip",
        "", n=24, ms=100)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    uni(f, 0, 0, "P25", LIME)
    for ch in range(6):
        x = 26 + ch * 6
        active = ch == 0 or (ch == 1 + (t * 5 // n) % 5)
        c = CYAN if ch == 0 else (LIME if active else (0, 40, 15))
        d.rectangle((x, 1, x + 4, 6), fill=c if active else BLACK, outline=c)
    syms = [3, 1, -1, -3, 1, 3, -3, -1, 1, -1, 3, -3]
    for x in range(26, 62):
        s = syms[((x - 26) // 3 + t) % 12] if n % 12 == 0 else 0
        d.point((x, 12 - s), fill=dim(LIME, 0.9))
    return f


@preset("p_rf_fmrds", "rf", "FM RDS ticker", "FM stereo pilot bars + an RDS-style radiotext ticker scrolling 'ORC FM' (art, not live RDS)",
        "", ms=70)
def _(t, n):
    msg = FMRDS_MSG
    strip = Image.new("RGB", (width35(msg) + 1, 5))
    text35(strip, 0, 0, msg, LIME)
    f = canvas(); d = ImageDraw.Draw(f)
    text35(f, 1, 1, "FM", CYAN)
    for i in range(4):
        h = 2 + round(2 * (0.5 + 0.5 * math.sin(TAU * t / n * (i + 1) + i)))
        d.line((10 + i * 2, 5, 10 + i * 2, 6 - h), fill=LIME if i != 2 else CYAN)
    text35(f, 22, 1, "19K PILOT", dim(CYAN, 0.6))
    wrap_scroll(strip, 3 * t, 0, 9, W, f)
    return f


FMRDS_MSG = " PS: ORC FM  RT: WARCHIEF'S TOP 40  "   # 36 chars -> 144 px (divisible by 3)


def _fmrds_frames():
    return (width35(FMRDS_MSG) + 1) // 3


@preset("p_rf_iq", "rf", "IQ constellation", "QPSK constellation slowly rotating with I and Q sine traces beside it",
        "", n=24, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.line((8, 1, 8, 15), fill=(0, 45, 20)); d.line((1, 8, 15, 8), fill=(0, 45, 20))
    rot = TAU * t / n / 4                                # quarter turn per loop = QPSK symmetric
    rnd = random.Random(4)
    for q in range(4):
        a = rot + math.pi / 4 + q * math.pi / 2
        for k in range(5):
            jx, jy = rnd.uniform(-0.8, 0.8), rnd.uniform(-0.8, 0.8)
            d.point((round(8 + 5 * math.cos(a) + jx), round(8 + 5 * math.sin(a) + jy)), fill=CYAN if k else WHITE)
    for x in range(18, 64):
        ph = TAU * ((x - 18) / 23 - t / n)
        d.point((x, round(4 + 2.5 * math.cos(ph))), fill=LIME)
        d.point((x, round(12 + 2.5 * math.sin(ph))), fill=CYAN)
    text35(f, 18, 6, "I", dim(LIME, 0.5)); text35(f, 60, 6, "Q", dim(CYAN, 0.5))
    return f


@preset("p_rf_smeter", "rf", "Analog S-meter", "Analog S-meter arc with a needle swinging S3..S9+20 and scale marks",
        "", n=24, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    cx, cy, r = 32, 22, 19
    for i in range(11):
        a = math.radians(210 + i * 12)
        c = LIME if i < 7 else RED
        d.line((cx + (r - 2) * math.cos(a), cy + (r - 2) * math.sin(a), cx + r * math.cos(a), cy + r * math.sin(a)), fill=c)
    v = 0.55 + 0.3 * math.sin(TAU * t / n) + 0.1 * math.sin(TAU * 3 * t / n + 0.5)
    a = math.radians(210 + v * 120)
    d.line((cx, cy, cx + (r - 3) * math.cos(a), cy + (r - 3) * math.sin(a)), fill=WHITE)
    text35(f, 1, 10, "S", CYAN); text35(f, 55, 10, "+20", RED)
    return f


@preset("p_rf_bandscan", "rf", "Band scanner", "Channel comb with a scan marker stepping across and pausing on 3 active channels",
        "", ms=70)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    active = {3: 0.9, 8: 0.6, 12: 0.8}
    seq = []
    for ch in range(16):
        seq += [ch] * (4 if ch in active else 1)
    ch = seq[t % len(seq)]
    for c in range(16):
        x = c * 4 + 1
        h = round(active.get(c, 0.15) * 9)
        d.line((x, 15, x, 15 - h), fill=LIME if c in active else DLIME)
        d.line((x + 1, 15, x + 1, 15 - h), fill=dim(LIME if c in active else DLIME, 0.5))
    x = ch * 4 + 1
    d.rectangle((x - 1, 5, x + 2, 15), outline=WHITE if ch in active else CYAN)
    text35(f, 1, 0, "SCAN", CYAN)
    text35(f, 20, 0, "CH " + f"{ch + 1:02d}", WHITE if ch in active else dim(CYAN, 0.6))
    return f


def _bandscan_n():
    return 16 + 3 * 3


@preset("p_rf_adsb", "rf", "ADS-B radar", "Radar sweep with aircraft blips that flare as the beam passes + 'ADS-B 1090'",
        "", n=16, ms=120)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    cx, cy = 8, 8
    ring(d, cx, cy, 7, (0, 55, 25)); ring(d, cx, cy, 3, (0, 40, 18))
    a = TAU * t / n
    for k in range(4):
        aa = a - k * 0.18
        d.line((cx, cy, cx + 7 * math.cos(aa), cy + 7 * math.sin(aa)), fill=dim(LIME, 1 - k * 0.25))
    for ba, br in [(0.6, 5), (2.4, 3), (4.1, 6), (5.3, 4)]:
        age = (a - ba) % TAU
        k = max(0.15, 1 - age / TAU * 1.5)
        d.point((round(cx + br * math.cos(ba)), round(cy + br * math.sin(ba))), fill=dim(WHITE, k))
    text35(f, 19, 1, "ADS-B", grad(19, 19))
    text35(f, 19, 9, "1090 MHZ", dim(CYAN, 0.7))
    return f


@preset("p_rf_lora", "rf", "LoRa chirps", "Up-chirp spectrogram: diagonal LoRa chirps sweeping and wrapping, + 'LORA'",
        "", n=32, ms=70)
def _(t, n):
    f = canvas(); px = f.load()
    for y in range(H):
        for x in range(18, 64):
            fx = ((y * 2 + t * 1.5) % 46)
            if abs((x - 18) - fx) < 0.8:
                px[x, y] = _lerp_stops([LIME, CYAN], (x - 18) / 45)
            elif (x * 7 + y * 13 + t) % 29 == 0:
                px[x, y] = (0, 50, 20)
    text35(f, 1, 1, "LORA", grad(1, 15))
    text35(f, 1, 9, "CSS", dim(LIME, 0.6))
    return f


@preset("p_rf_multirate", "rf", "DSP multirate", "Three sample streams at 1x, 1/2 and 1/4 rate flowing with 'DEC' decimation labels",
        "", n=32, ms=70)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    for row, (step, lab, c) in enumerate([(2, "1X", LIME), (4, "/2", CYAN), (8, "/4", WHITE)]):
        y = 2 + row * 5
        text35(f, 0, y - 1 + (1 if row == 0 else 0), lab, dim(c, 0.6))
        for x in range(13, 64):
            s = math.sin(TAU * ((x + 2 * t) / 32))           # waveform scrolls 64 px/loop
            if (x + 2 * t) % step == 0:
                d.line((x, y + 2, x, y + 2 - round(s * 1.5)), fill=c)
            else:
                d.point((x, y + 2), fill=(0, 30, 12))
    return f


@preset("p_rf_m5dial", "rf", "M5Dial VFO", "VFO knob rocking back and forth with a tuning readout that follows it",
        "", n=32, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    ring(d, 8, 8, 7, dim(LIME, 0.6)); ring(d, 8, 8, 5, (0, 50, 20))
    s = math.cos(TAU * t / n)                    # loop boundary at a turning point
    a = -math.pi / 2 + s * 2.2
    d.line((8, 8, 8 + 5 * math.cos(a), 8 + 5 * math.sin(a)), fill=WHITE)
    for i in range(12):
        aa = TAU * i / 12
        d.point((round(8 + 7 * math.cos(aa)), round(8 + 7 * math.sin(aa))), fill=CYAN)
    khz = 145500 + round(s * 25) * 5
    text35(f, 19, 1, f"{khz // 1000}.{khz % 1000:03d}", grad(19, 27))
    text35(f, 19, 9, "VFO A  FM", dim(CYAN, 0.7))
    return f


# ================================================================= orc
def big_orc(scale_w=16):
    return orc_icon()


@preset("p_orc_eyes", "orc", "Warchief's gaze", "Orc face whose eyes glow brighter and blink once per loop, 'WARCHIEF' beside it",
        "", n=24, ms=90)
def _(t, n):
    f = canvas(); f.paste(orc_icon(), (0, 0))
    glow = 0.5 + 0.5 * math.sin(TAU * t / n)
    blink = t in (n // 2, n // 2 + 1)
    for x in (6, 9):
        f.putpixel((x, 7), (0, 0, 0) if blink else _lerp_stops([(120, 160, 60), (255, 255, 150)], glow))
    text35(f, 19, 2, "WARCHIEF", grad(19, 31))
    text35(f, 19, 9, "IS WATCHING", dim(LIME, 0.35 + 0.4 * glow))
    return f


@preset("p_orc_drums", "orc", "War drums", "Two war drums pounding in a 4-beat rhythm with impact rings",
        "", n=16, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    for i, cx in enumerate((12, 52)):
        beat = (t - i * 2) % 4
        d.ellipse((cx - 7, 6, cx + 7, 10), outline=AMBER)
        d.rectangle((cx - 7, 8, cx + 7, 14), outline=(150, 90, 20))
        d.line((cx - 7, 8, cx - 7, 14), fill=(150, 90, 20))
        if beat < 2:
            r = 2 + beat * 3
            d.arc((cx - r - 7, 8 - r, cx + r + 7, 8 + r), 180, 360, fill=dim(LIME, 1 - beat * 0.5))
        sy = 1 if beat == 0 else 3
        d.line((cx + 2, sy, cx + 5, sy + 4), fill=WHITE)
    text35(f, 23, 5, "BOOM", LIME if t % 4 == 0 else DLIME)
    return f


def flame(d, cx, base, t, n, k):
    for i in range(6):
        h = 4 + 2 * math.sin(TAU * t / n * 3 + k + i) + math.sin(TAU * t / n * 5 + i * 2)
        x = cx - 2 + i * 0.8
        d.line((round(x), base, round(x), round(base - h)), fill=_lerp_stops([RED, AMBER, (255, 240, 120)], i / 6))


@preset("p_orc_torches", "orc", "Torch-lit hall", "Two flickering torches framing 'WARCHIEF' in the hall",
        "", n=12, ms=100)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    for k, cx in enumerate((4, 59)):
        d.line((cx, 15, cx, 9), fill=(110, 70, 30))
        flame(d, cx, 8, t, n, k * 2.1)
    glow = 0.8 + 0.2 * math.sin(TAU * t / n * 3)
    text35(f, 16, 5, "WARCHIEF", lambda gx, gy: dim(_lerp_stops([AMBER, LIME], (gx - 16) / 30), glow))
    return f


@preset("p_orc_banner", "orc", "Warband banner", "A waving war banner with the orc emblem rippling in the wind",
        "", n=12, ms=110)
def _(t, n):
    base = Image.new("RGB", (40, 14), (20, 60, 15))
    base.paste(orc_icon().resize((12, 12), Image.Resampling.NEAREST), (14, 1))
    ImageDraw.Draw(base).rectangle((0, 0, 39, 13), outline=LIME)
    f = canvas(); d = ImageDraw.Draw(f)
    d.line((10, 0, 10, 15), fill=(160, 120, 70))
    for x in range(40):
        dy = round(1.5 * math.sin(TAU * (x / 20 - t / n)) * (x / 40))
        col = base.crop((x, 0, x + 1, 14))
        shade = 0.75 + 0.25 * math.cos(TAU * (x / 20 - t / n))
        col = col.point(lambda p: int(p * shade))
        f.paste(col, (11 + x, 1 + dy))
    return f


ORC_SPRITE = ["..##..", ".####.", "#.##.#", ".####.", ".#..#.", ".#..#."]
ORC_SPRITE2 = ["..##..", ".####.", "#.##.#", ".####.", "..##..", ".#..#."]


@preset("p_orc_march", "orc", "Warband march", "A column of tiny orcs with spears marching across and wrapping",
        "", n=16, ms=100)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.line((0, 15, 63, 15), fill=(40, 30, 15))
    for k in range(4):
        x0 = (k * 16 + t) % 64
        spr = ORC_SPRITE if t % 2 == 0 else ORC_SPRITE2
        for y, r in enumerate(spr):
            for x, c in enumerate(r):
                if c == "#":
                    f.putpixel(((x0 + x) % 64, 9 + y), LIME)
        f.putpixel(((x0 + 2) % 64, 10), (255, 255, 150))
        d.line(((x0 + 6) % 64, 5, (x0 + 6) % 64, 14), fill=(170, 130, 80)) if (x0 + 6) % 64 > 0 else None
        f.putpixel(((x0 + 6) % 64, 4), WHITE)
    text35(f, 1, 0, "WARBAND", dim(CYAN, 0.8))
    return f


RUNES = ["#.#.#", "###.#", "#...#", ".#.#.", "##..#", "#.###", "..#..", "#.#..", ".##.#", "###..", "#..##", ".#.##"]


@preset("p_orc_runes", "orc", "Orcish Tongue", "A ribbon of glowing orcish runes scrolling beneath 'ORCISH' (seamless wrap)",
        "", ms=70)
def _(t, n):
    strip = Image.new("RGB", (48, 7))
    rnd = random.Random(12)
    for i in range(12):
        g = [RUNES[rnd.randrange(12)] for _ in range(5)]
        for y in range(5):
            for x in range(3):
                if g[y][x + (y % 3)] == "#" if x + (y % 3) < 5 else False:
                    strip.putpixel((i * 4 + x, 1 + y), _lerp_stops([LIME, CYAN], i / 11))
    f = canvas()
    text35(f, 1, 1, "ORCISH", grad(1, 23))
    text35(f, 28, 1, "TONGUE", dim(LIME, 0.6))
    return wrap_scroll(strip, 2 * t, 0, 8, W, f)


@preset("p_orc_horn", "orc", "War horn", "A war horn blasting sound waves toward 'AROOO'",
        "", n=16, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.polygon([(1, 7), (1, 10), (10, 13), (12, 4)], fill=(200, 170, 110))
    d.line((12, 4, 12, 13), fill=WHITE)
    for k in range(3):
        r = ((t / n + k / 3) % 1.0) * 14 + 2
        d.arc((12 - r, 8 - r, 12 + r, 8 + r), -50, 50, fill=dim(LIME, 1 - r / 17))
    text35(f, 36, 5, "AROOO", LIME if (t // 4) % 2 == 0 else CYAN)
    return f


@preset("p_orc_forge", "orc", "Orc forge", "Hammer striking an anvil with a burst of sparks, 'FORGE'",
        "", n=16, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.polygon([(3, 11), (21, 11), (18, 13), (6, 13)], fill=(120, 120, 130)); d.rectangle((9, 13, 15, 15), fill=(90, 90, 100))
    ph = t / n * 2 % 1.0
    ang = -0.9 + 1.0 * abs(math.cos(math.pi * ph))
    hx, hy = 14 + 8 * math.cos(ang - 1.2), 10 + 8 * math.sin(ang - 1.2)
    d.line((22, 12, hx, hy), fill=(150, 110, 60)); d.rectangle((hx - 2, hy - 1, hx + 1, hy + 1), fill=(170, 170, 180))
    for s in range(8):
        age = (ph - 0.5) % 1.0
        if age < 0.45:
            a = -math.pi / 2 + (s - 3.5) * 0.35
            r = age * 25
            d.point((round(12 + r * math.cos(a)), round(10 + r * math.sin(a) + age * age * 20)), fill=AMBER if s % 2 else (255, 240, 120))
    text35(f, 35, 5, "FORGE", grad(35, 19, [AMBER, LIME]))
    return f


@preset("p_orc_advisor", "orc", "Warchief's Advisor", "Orc icon fixed left, 'THE WARCHIEF'S ADVISOR' scrolling beside it (seamless)",
        "", ms=70)
def _(t, n):
    msg = ADVISOR_MSG
    strip = Image.new("RGB", (width35(msg) + 1, 16))
    text35(strip, 0, 5, msg, lambda gx, gy: CYAN if gx >= width35("THE WARCHIEF'S ") else LIME)
    f = canvas()
    wrap_scroll(strip, 3 * t, 17, 0, 47, f)
    f.paste(orc_icon(), (0, 0))
    return f


ADVISOR_MSG = "THE WARCHIEF'S ADVISOR  "          # 24 chars -> 96 px


@preset("p_orc_tusks", "orc", "Stay Orc", "Orc icon with a tusk gleam and 'STAY ORC' pulsing",
        "", n=16, ms=90)
def _(t, n):
    f = canvas(); f.paste(orc_icon(), (0, 0))
    g = t % n
    if g in (3, 4, 5):
        f.putpixel((2 + (g - 3), 10 + (g - 3)), (255, 255, 255))
        f.putpixel((13 - (g - 3), 10 + (g - 3)), (255, 255, 255))
    k = 0.55 + 0.45 * (0.5 + 0.5 * math.cos(TAU * t / n))
    text35(f, 20, 2, "STAY", dim(LIME, k))
    text35(f, 20, 9, "ORC", dim(CYAN, k))
    return f


# ================================================================ code
@preset("p_code_compiling", "code", "Compiling", "'COMPILING' with cycling dots and an indeterminate progress block sliding (wrap)",
        "", n=24, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    dots = "." * (1 + (t * 3 // n) % 3) if n else ""
    text35(f, 1, 1, "COMPILING" + dots, LIME)
    d.rectangle((1, 10, 62, 14), outline=(0, 60, 25))
    x = (t / n) * 70 - 10
    for i in range(12):
        xx = round(x + i)
        if 2 <= xx <= 61:
            d.line((xx, 11, xx, 13), fill=_lerp_stops([DLIME, LIME, CYAN], i / 11))
    return f


@preset("p_code_tests_pass", "code", "Tests pass", "'TESTS PASS' with a marquee of green check marks gliding underneath",
        "", ms=80)
def _(t, n):
    strip = Image.new("RGB", (32, 6))
    d = ImageDraw.Draw(strip)
    for k in range(4):
        x = k * 8
        d.line((x + 1, 3, x + 2, 4), fill=LIME); d.line((x + 2, 4, x + 5, 1), fill=LIME)
    f = canvas()
    text35(f, 1, 1, "TESTS", WHITE); text35(f, 22, 1, "PASS", LIME)
    text35(f, 40, 1, "OK", CYAN if t % 8 < 4 else dim(CYAN, 0.5))
    return wrap_scroll(strip, t, 0, 9, W, f)


@preset("p_code_tests_fail", "code", "Tests fail", "Red X blinking beside '1 FAILED' with a red scanline (variety, still on-brand black)",
        "", n=16, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    on = (t // 4) % 2 == 0
    c = RED if on else dim(RED, 0.4)
    d.line((2, 3, 11, 12), fill=c, width=2); d.line((11, 3, 2, 12), fill=c, width=2)
    text35(f, 17, 2, "1 FAILED", RED)
    text35(f, 17, 9, "SEE LOG", dim(WHITE, 0.5))
    y = t % 16
    d.line((15, y, 63, y), fill=(60, 0, 0))
    return f


@preset("p_code_deploy", "code", "Deploy to edge", "Packets flying from a PC to an ESP32 node: 'DEPLOY TO EDGE' (no cloud)",
        "", n=16, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.rectangle((1, 7, 10, 13), outline=CYAN); d.line((4, 14, 7, 14), fill=CYAN); d.line((3, 15, 8, 15), fill=CYAN)
    d.rectangle((53, 8, 62, 15), outline=LIME)
    for i in range(3):
        d.point((55 + i * 3, 7), fill=LIME)
    for k in range(3):
        x = 12 + ((t / n + k / 3) % 1.0) * 39
        y = 11 - 3 * math.sin(math.pi * (x - 12) / 39)
        d.rectangle((round(x), round(y), round(x) + 1, round(y) + 1), fill=WHITE if k == 0 else CYAN)
    text35(f, 1, 1, "DEPLOY", LIME); text35(f, 26, 1, "TO EDGE", CYAN)
    return f


@preset("p_code_codex", "code", "Codex thinking", "'CODEX' with three thinking dots bouncing in a wave",
        "", n=16, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    uni(f, 2, 0, "Codex", (230, 230, 230))
    for k in range(3):
        y = 9 - round(3 * max(0, math.sin(TAU * t / n - k * 0.8)))
        d.rectangle((46 + k * 6, y, 47 + k * 6, y + 1), fill=[LIME, CYAN, WHITE][k])
    return f


@preset("p_code_claude", "code", "Claude spark", "An 8-ray spark slowly rotating beside 'CLAUDE' (orange accent variety)",
        "", n=16, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    rot = (TAU / 8) * t / n                                # 1/8 turn per loop (8-fold symmetric)
    for k in range(8):
        a = rot + TAU * k / 8
        r = 6 if k % 2 == 0 else 4
        d.line((8, 8, 8 + r * math.cos(a), 8 + r * math.sin(a)), fill=(217, 119, 87))
    d.point((8, 8), fill=WHITE)
    uni(f, 17, 0, "Claude", (240, 200, 180))
    return f


@preset("p_code_grok", "code", "Grok orbit", "'GROK' with a particle orbiting a ring once per loop",
        "", n=24, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    ring(d, 8, 8, 6, (70, 70, 90))
    for k in range(4):
        a = TAU * t / n - k * 0.25
        d.point((round(8 + 6 * math.cos(a)), round(8 + 6 * math.sin(a))), fill=dim(WHITE, 1 - k * 0.25))
    d.line((5, 11, 11, 5), fill=WHITE)
    uni(f, 20, 0, "Grok", (230, 230, 255))
    return f


@preset("p_code_gitmerge", "code", "Git merge", "Two branch lanes with commits flowing left and merging, '+ MERGE'",
        "", ms=80)
def _(t, n):
    strip = Image.new("RGB", (32, 16)); d = ImageDraw.Draw(strip)
    d.line((0, 12, 31, 12), fill=LIME)
    d.line((0, 6, 12, 6), fill=CYAN); d.line((12, 6, 20, 12), fill=CYAN)
    for x in (4, 20, 28):
        d.ellipse((x - 1, 11, x + 1, 13), fill=WHITE)
    d.ellipse((5, 5, 7, 7), fill=CYAN)
    f = canvas()
    wrap_scroll(strip, t, 0, 0, W, f)
    ImageDraw.Draw(f).rectangle((40, 0, 63, 6), fill=BLACK)
    text35(f, 41, 1, "MERGE", LIME)
    return f


@preset("p_code_terminal", "code", "Orc terminal", "A tiny terminal: build log lines scrolling up under a blinking '>' prompt",
        "", ms=100)
def _(t, n):
    lines = ["> ORC BUILD", "CMAKE OK", "LINK P5A", "ASAN CLEAN", "TESTS 18/18", "FLASH P4", "DONE"]
    strip = Image.new("RGB", (W, 6 * len(lines)))
    for i, s in enumerate(lines):
        text35(strip, 1, i * 6, s, LIME if i == 0 else (CYAN if "OK" in s or "DONE" in s or "CLEAN" in s else dim(LIME, 0.7)))
    f = canvas()
    off = t % strip.height
    tall = Image.new("RGB", (W, strip.height * 2)); tall.paste(strip, (0, 0)); tall.paste(strip, (0, strip.height))
    f.paste(tall.crop((0, off, W, off + H)), (0, 0))
    if (t // 3) % 2 == 0:
        ImageDraw.Draw(f).rectangle((60, 11, 62, 15), fill=WHITE)
    return f


@preset("p_code_llm", "code", "Local LLM tokens", "'LOCAL AI' with a token stream of varied-width blocks flowing through",
        "", ms=60)
def _(t, n):
    strip = Image.new("RGB", (64, 6)); d = ImageDraw.Draw(strip)
    rnd = random.Random(40)
    x = 0
    while x < 62:
        w = rnd.choice([2, 3, 4, 5])
        if x + w > 63:
            break
        d.rectangle((x, 1, x + w - 1, 4), fill=_lerp_stops([DLIME, LIME, CYAN, WHITE], rnd.random()))
        x += w + 1
    f = canvas()
    text35(f, 1, 1, "LOCAL AI", grad(1, 31))
    text35(f, 36, 1, "GGUF", dim(CYAN, 0.7))
    return wrap_scroll(strip, 4 * t, 0, 9, W, f)


# ================================================================== hw
@preset("p_hw_esp32p4", "hw", "ESP32-P4 chip", "QFN chip labelled 'ESP32-P4' with LEDs chasing around its pins",
        "", ms=60)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.rectangle((12, 2, 51, 13), fill=(25, 25, 30), outline=(90, 90, 100))
    text35(f, 16, 5, "ESP32-P4", grad(16, 31))
    pins = [(x, 0) for x in range(13, 51, 2)] + [(53, y) for y in range(3, 13, 2)] + \
           [(x, 15) for x in range(50, 12, -2)] + [(10, y) for y in range(12, 2, -2)]
    for i, (x, y) in enumerate(pins):
        k = (i - 2 * t) % len(pins)
        c = WHITE if k == 0 else CYAN if k < 3 else LIME if k < 6 else (70, 70, 80)
        d.point((x, y), fill=c)
        if y in (0, 15):
            d.point((x, 1 if y == 0 else 14), fill=dim(c, 0.6))
    return f


def _pins_n():
    return (len(range(13, 51, 2)) * 2 + len(range(3, 13, 2)) * 2) // 2


@preset("p_hw_antenna", "hw", "Antenna", "Mast antenna radiating wavefronts to both sides + 'RTL-SDR V4'",
        "", n=16, ms=90)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.line((8, 15, 8, 3), fill=(180, 180, 190)); d.line((5, 15, 11, 15), fill=(180, 180, 190)); d.point((8, 2), fill=RED)
    for k in range(3):
        r = ((t / n + k / 3) % 1.0) * 8 + 1
        d.arc((8 - r, 2 - r, 8 + r, 2 + r), 0, 360, fill=dim(LIME, 1 - r / 9.5))
    text35(f, 21, 2, "RTL-SDR", grad(21, 27))
    text35(f, 21, 9, "BLOG V4", dim(CYAN, 0.7))
    return f


@preset("p_hw_uart", "hw", "UART bits", "TX line: the bits of 'Orc' (8N1) as a square wave scrolling past, 'TX 115200'",
        "", ms=60)
def _(t, n):
    bits = []
    for ch in "Orc":
        b = ord(ch)
        bits += [0] + [(b >> i) & 1 for i in range(8)] + [1, 1]
    strip = Image.new("RGB", (len(bits) * 3, 8)); d = ImageDraw.Draw(strip)
    prev = 1
    for i, b in enumerate(bits):
        y = 1 if b else 6
        d.line((i * 3, y, i * 3 + 2, y), fill=LIME)
        if b != prev:
            d.line((i * 3, 1, i * 3, 6), fill=LIME)
        prev = b
    f = canvas()
    text35(f, 1, 1, "TX", CYAN); text35(f, 12, 1, "115200 8N1", dim(LIME, 0.6))
    return wrap_scroll(strip, 3 * t, 0, 8, W, f)


@preset("p_hw_pcb", "hw", "PCB traces", "Circuit traces (from the OrcPixels logo border) with light pulses running along them",
        "", n=16, ms=80)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    paths = [[(0, 3), (12, 3), (16, 7), (30, 7), (34, 3), (63, 3)],
             [(0, 12), (20, 12), (24, 8), (40, 8), (44, 12), (63, 12)],
             [(0, 8), (8, 8), (10, 10), (52, 10), (56, 6), (63, 6)]]
    for p in paths:
        d.line(p, fill=(0, 60, 25))
    for i, p in enumerate(paths):
        segs = list(zip(p, p[1:]))
        L = [math.dist(a, b) for a, b in segs]
        tot = sum(L)
        pos = ((t / n + i / 3) % 1.0) * tot
        for a, b in [(0, 1.0), (-2, 0.5), (-4, 0.25)]:
            s = (pos + a) % tot
            for (p0, p1), l in zip(segs, L):
                if s <= l:
                    x = p0[0] + (p1[0] - p0[0]) * s / l; y = p0[1] + (p1[1] - p0[1]) * s / l
                    d.point((round(x), round(y)), fill=dim(CYAN if i % 2 else LIME, b) if a else WHITE)
                    break
                s -= l
    for x, y in [(12, 3), (34, 3), (24, 8), (44, 12), (10, 10), (56, 6)]:
        d.point((x, y), fill=LIME)
    return f


@preset("p_hw_tab5", "hw", "Tab5 SDR", "A tiny M5Stack Tab5 outline whose screen runs a looping waterfall",
        "", n=16, ms=100)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    d.rounded_rectangle((0, 0, 29, 15), radius=2, outline=(150, 150, 160))
    wf = _TAB5_WF
    f.paste(wf.image(t), (2, 2))
    text35(f, 33, 2, "TAB5", grad(33, 15))
    text35(f, 33, 9, "ORCSDR", dim(LIME, 0.7))
    return f


_TAB5_WF = LoopWaterfall(26, 12, 16, seed=55, carriers=((0.3, 0.8, 0.0, 1), (0.7, 0.5, 0.05, 1)))


# ================================================================= amb
@preset("p_amb_aurora", "amb", "Lime aurora", "Slow lime/cyan aurora curtains drifting (ambient, text-free)",
        "", n=24, ms=110)
def _(t, n):
    f = canvas(); px = f.load()
    for x in range(W):
        for y in range(H):
            a = TAU * t / n
            c = 7 + 3 * math.sin(TAU * x / 64 + a) + 2 * math.sin(TAU * 2 * x / 64 - a)
            v = max(0.0, 1 - abs(y - c) / 3.5) * (0.6 + 0.4 * math.sin(TAU * 3 * x / 64 + 2 * a))
            if v > 0.1:
                px[x, y] = _lerp_stops([BLACK, DLIME, LIME, CYAN], round(min(1, v) * 5) / 5)
    return f


@preset("p_amb_plasma", "amb", "Brand plasma", "Classic demoscene plasma in black/green/lime/cyan (ambient)",
        "", n=24, ms=90)
def _(t, n):
    f = canvas(); px = f.load()
    a = TAU * t / n
    for x in range(W):
        for y in range(H):
            v = math.sin(x / 5 + a) + math.sin(y / 3 - a) + math.sin((x + y) / 7 + 2 * a) + math.sin(math.hypot(x - 32, y - 8) / 4 - a)
            q = round(((v + 4) / 8) * 7) / 7
            px[x, y] = _lerp_stops([BLACK, (0, 50, 20), DLIME, LIME, CYAN, (0, 60, 80)], q)
    return f


@preset("p_amb_starfield", "amb", "Starfield", "Parallax starfield drifting left at 3 speeds (ambient, seamless wrap)",
        "", n=32, ms=70)
def _(t, n):
    f = canvas()
    rnd = random.Random(77)
    for layer, (sp, col) in enumerate([(2, (60, 90, 70)), (4, CYAN), (6, WHITE)]):
        for _ in range(12 - layer * 3):
            x0, y = rnd.randrange(64), rnd.randrange(16)
            if sp == 6:
                sp_ = 6
            f.putpixel(((x0 - sp * t) % 64, y), col)
    return f


@preset("p_amb_lissajous", "amb", "Lissajous", "A 1:2 Lissajous figure-eight with a glowing trail and slowly turning phase",
        "", n=32, ms=70)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    ph = TAU * t / n
    X = lambda s: 32 + 29 * math.sin(s + ph)
    Y = lambda s: 7.5 + 6.5 * math.sin(2 * s)
    d.line([(X(s), Y(s)) for s in [TAU * i / 120 for i in range(121)]], fill=(0, 90, 40))
    head = TAU * t / n * 2
    for k in range(14):
        s = head - k * 0.07
        d.point((round(X(s)), round(Y(s))), fill=_lerp_stops([WHITE, CYAN, LIME, DLIME], k / 13))
    return f


@preset("p_amb_heartbeat", "amb", "RF heartbeat", "ECG-style lime pulse trace scrolling (the node is alive)",
        "", n=32, ms=70)
def _(t, n):
    f = canvas(); d = ImageDraw.Draw(f)
    def yv(u):
        u %= 32
        return {12: 6, 13: 2, 14: 13, 15: 8}.get(u, 9)
    pts = [(x, yv(x + 2 * t)) for x in range(W)]
    d.line(pts, fill=LIME)
    for k in range(3):
        x = (62 - k)
        d.point((x, yv(x + 2 * t)), fill=WHITE if k == 0 else CYAN)
    return f


# frame-count overrides for presets whose loop length follows their content
AUTO_N = {
    "p_rf_fmrds": _fmrds_frames, "p_rf_bandscan": _bandscan_n, "p_orc_runes": lambda: 24,
    "p_orc_advisor": lambda: (width35(ADVISOR_MSG) + 1) // 3,
    "p_code_tests_pass": lambda: 32, "p_code_gitmerge": lambda: 32, "p_code_terminal": lambda: 42,
    "p_code_llm": lambda: 16, "p_hw_esp32p4": _pins_n, "p_hw_uart": lambda: 33,
}


def build(entry) -> dict:
    n = entry["n"] or AUTO_N[entry["name"]]()
    frames = [entry["fn"](t, n) for t in range(n)]
    gif = encode_gif(frames, entry["ms"])
    name = entry["name"]
    (OUT / f"{name}.gif").write_bytes(gif)
    rep = max(frames, key=lambda f: sum(f.convert("L").tobytes()))
    rep.save(OUT / f"{name}.png")
    rep.resize((640, 160), Image.Resampling.NEAREST).save(OUT / f"{name}_preview_10x.png")
    meta = {"name": name, "kind": "gif", "file": f"{name}.gif", "still": f"{name}.png",
            "preview": f"{name}_preview_10x.png", "family": entry["family"], "title": entry["title"],
            "description": entry["desc"], "frames": n, "frame_ms": entry["ms"],
            "bytes": len(gif), "loop": "seamless (tools/loopcheck.py)", "live_data": False,
            "generator": f"tools/make_personal_presets.py::{name}"}
    (OUT / f"{name}.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return {**meta, "_rep": rep}


FAMILY_TITLES = {"proj": "Projects", "rf": "RF / SDR", "orc": "Orc / Warchief", "code": "Coding & AI",
                 "hw": "Hardware + Ambient", "amb": "Hardware + Ambient"}


def sheets(results):
    lab = ImageFont.truetype(str(ROOT / "orcpixels/fonts/unifont.otf"), 16)
    groups: dict[str, list] = {}
    for r in results:
        key = "hw_amb" if r["family"] in ("hw", "amb") else r["family"]
        groups.setdefault(key, []).append(r)
    paths = []
    for key, rs in groups.items():
        pad, lh = 12, 22
        sheet = Image.new("RGB", (640 + 2 * pad, 26 + len(rs) * (160 + lh + pad) + pad), (28, 28, 28))
        d = ImageDraw.Draw(sheet)
        d.text((pad, 4), f"OrcPixels personal presets: {FAMILY_TITLES[rs[0]['family']]}", fill=(255, 255, 255), font=lab)
        y = 26 + pad
        for r in rs:
            d.text((pad, y), f"{r['name']}  {r['title']}  ({r['frames']}f, {r['bytes'] / 1024:.1f} KB)", fill=(200, 255, 170), font=lab)
            sheet.paste(r["_rep"].resize((640, 160), Image.Resampling.NEAREST), (pad, y + lh))
            y += 160 + lh + pad
        p = OUT / f"orcpixels_personal_sheet_{key}.png"
        sheet.save(p)
        paths.append(p)
    return paths


def main(argv):
    sel = [e for e in REGISTRY if not argv or e["name"] in argv]
    results = []
    for e in sel:
        r = build(e)
        results.append(r)
        print(f"{r['name']:24} {r['family']:5} {r['frames']:3}f {r['bytes']:6} B  {r['title']}")
    if not argv:
        for p in sheets(results):
            print("sheet", p.relative_to(ROOT))
    print(len(results), "presets")


if __name__ == "__main__":
    main(sys.argv[1:])
