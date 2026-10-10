"""64x64 versions of the 50 tailored presets (families amb, code, hw, orc, proj, rf)."""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw

from p64lib import (AMBER, BLACK, CYAN, DCYAN, DLIME, GRAD, LIME, LIME2, MAG, RED, TAU, WHITE, WF_MAP,
                    canvas, dim, grad, levels, lv_anim, mix, orc, p64, paint_mask, put, ring, scroll, spectrum,
                    stars, t35, t35c, twinkle, uni, uni_mask, unic, uw, waterfall, width35)
from orcpixels.render import _lerp_stops

ORANGE = (255, 120, 40)
GREEN = (60, 230, 90)


def pal(v, stops):
    return _lerp_stops(stops, max(0.0, min(1.0, v)))


# ====================================================================== amb
@p64("p_amb_aurora", n=24, ms=110, note="full-field lime/cyan aurora curtains")
def aurora(t, n):
    f = canvas()
    px = f.load()
    ph = TAU * t / n
    for x in range(64):
        a = 0.5 + 0.25 * math.sin(x * 0.17 + ph) + 0.25 * math.sin(x * 0.07 - 2 * ph + 1.3)
        top = 6 + int(14 * (1 - a))
        ln = 20 + int(26 * a)
        for y in range(top, min(64, top + ln)):
            k = 1 - (y - top) / ln
            v = k * (0.5 + 0.5 * a)
            q = round(v * 6) / 6
            if q > 0:
                px[x, y] = pal(q, [(0, 0, 0), (0, 60, 40), (40, 200, 90), LIME, CYAN])
    for x, y, p in stars(5, 18, 64, 18):
        if px[x, y] == (0, 0, 0):
            px[x, y] = dim((150, 160, 190), 0.4 + 0.4 * (0.5 + 0.5 * math.cos(ph + TAU * p)))
    return f


def _ecg(u):
    """One heartbeat over u in [0,1)."""
    for c, h, w in ((0.18, -0.12, 0.03), (0.30, -0.08, 0.008), (0.33, 1.0, 0.012), (0.36, -0.3, 0.01), (0.55, 0.22, 0.05)):
        if abs(u - c) < w * 3:
            return h * math.exp(-((u - c) / w) ** 2)
    return 0.0


@p64("p_amb_heartbeat", n=32, ms=70, note="ECG trace over a dotted monitor grid, two beats wide")
def heartbeat(t, n):
    f = canvas()
    for x in range(0, 64, 8):
        for y in range(4, 60, 2):
            put(f, x, y, (10, 32, 10))
    for y in range(4, 61, 8):
        for x in range(0, 64, 2):
            put(f, x, y, (10, 32, 10))
    d = ImageDraw.Draw(f)
    last = None
    head = 63
    for x in range(64):
        u = ((x + 2 * t) % 32) / 32
        y = 36 - 26 * sum(_ecg((u + k) % 1) for k in (0,))
        p = (x, y)
        if last:
            age = (head - x) / 64
            d.line((last, p), fill=mix(WHITE, LIME, min(1, 0.2 + age * 2)))
        last = p
    return f


@p64("p_amb_lissajous", n=32, ms=70, note="1:2 Lissajous figure with glowing trail, phase turns once")
def lissajous(t, n):
    f = canvas()
    ph = TAU * t / n
    pts = []
    for i in range(160):
        s = TAU * i / 160
        pts.append((32 + 28 * math.sin(s + ph), 32 + 26 * math.sin(2 * s)))
    d = ImageDraw.Draw(f)
    for i in range(160):
        a, b = pts[i], pts[(i + 1) % 160]
        d.line((a, b), fill=DLIME)
    hi = int(160 * t / n)
    for j in range(24):
        a, b = pts[(hi - j) % 160], pts[(hi - j + 1) % 160]
        d.line((a, b), fill=mix(WHITE, LIME, j / 24))
    return f


@p64("p_amb_plasma", n=24, ms=90, note="demoscene plasma, full field, brand colours")
def plasma(t, n):
    f = canvas()
    px = f.load()
    ph = TAU * t / n
    stops = [(0, 0, 0), (0, 70, 20), (40, 200, 60), LIME, CYAN, (0, 60, 90), (0, 0, 0)]
    for y in range(64):
        for x in range(64):
            v = (math.sin(x / 7 + ph) + math.sin(y / 9 - ph) + math.sin((x + y) / 11 + 2 * ph)
                 + math.sin(math.hypot(x - 32, y - 32) / 6 - ph)) / 8 + 0.5
            px[x, y] = pal(round(v * 10) / 10, stops)
    return f


@p64("p_amb_starfield", n=32, ms=70, note="three-layer parallax starfield drifting left")
def starfield(t, n):
    f = canvas()
    for layer, (count, speed, col) in enumerate(((40, 1, (70, 80, 110)), (22, 2, (150, 160, 200)), (10, 4, WHITE))):
        for x, y, _ in stars(10 + layer, count):
            xx = (x - speed * 64 * t // n) % 64
            put(f, xx, y, col)
            if speed == 4:
                put(f, (xx + 1) % 64, y, dim(col, 0.4))
    return f


# ====================================================================== code
@p64("p_code_claude", n=16, ms=90, note="big 8-ray spark rotating above 'CLAUDE'")
def claude(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    a0 = (TAU / 4) * t / n
    cx, cy = 32, 22
    for i in range(8):
        a = a0 + i * TAU / 8
        r = 18 if i % 2 == 0 else 12
        d.line((cx, cy, cx + r * math.cos(a), cy + r * math.sin(a)), fill=ORANGE, width=3 if i % 2 == 0 else 2)
    d.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=(255, 190, 120))
    unic(f, 44, "CLAUDE", grad(8, 48, [(255, 190, 120), ORANGE]))
    return f


@p64("p_code_codex", n=16, ms=80, note="'CODEX' in a chat bubble with three dots bouncing in a wave")
def codex(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rounded_rectangle((4, 26, 59, 52), radius=8, outline=dim(WHITE, 0.6))
    d.polygon([(14, 52), (20, 52), (12, 59)], fill=dim(WHITE, 0.6))
    for i in range(3):
        y = 39 - 6 * max(0, math.sin(TAU * (t / n - i / 6)))
        d.ellipse((17 + 13 * i - 3, y - 3, 17 + 13 * i + 3, y + 3), fill=mix(WHITE, CYAN, i / 2))
    unic(f, 4, "CODEX", grad(12, 40, [WHITE, CYAN]))
    return f


@p64("p_code_compiling", n=24, ms=80, note="rotating gear, 'COMPILING' + dots, sliding progress block")
def compiling(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    cx, cy, a0 = 32, 20, (TAU / 8) * t / n
    for i in range(8):
        a = a0 + i * TAU / 8
        d.line((cx + 9 * math.cos(a), cy + 9 * math.sin(a), cx + 15 * math.cos(a), cy + 15 * math.sin(a)), fill=LIME, width=4)
    d.ellipse((cx - 11, cy - 11, cx + 11, cy + 11), fill=LIME)
    d.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=BLACK)
    x = t35c(f, 42, "COMPILING", CYAN)
    for i in range(3):
        put(f, 50 + 3 * i if False else 52 + 3 * i, 46, CYAN if i < (t * 4 // n) % 4 else DCYAN)
    d.rectangle((2, 54, 61, 59), outline=dim(LIME, 0.5))
    bx = int((t / n) * 72) - 12
    if bx + 12 >= 4 and bx <= 59:
        d.rectangle((max(4, bx), 56, min(59, bx + 12), 57), fill=LIME)
    _ = x
    return f


@p64("p_code_deploy", n=16, ms=80, note="packets arc from a PC to an ESP32 node, 'DEPLOY TO EDGE'")
def deploy(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rectangle((2, 24, 18, 36), outline=CYAN)
    d.rectangle((4, 26, 16, 34), fill=DCYAN)
    d.line((10, 37, 10, 40), fill=CYAN)
    d.line((5, 40, 15, 40), fill=CYAN)
    d.rectangle((46, 24, 60, 38), outline=LIME)
    for k in range(4):
        d.line((44, 26 + 3 * k, 45, 26 + 3 * k), fill=LIME)
        d.line((61, 26 + 3 * k, 62, 26 + 3 * k), fill=LIME)
    t35(f, 48, 29, "P4", LIME)
    for k in range(3):
        u = (t / n + k / 3) % 1
        x = 20 + u * 24
        y = 30 - 14 * math.sin(math.pi * u)
        d.rectangle((x - 1, y - 1, x + 1, y + 1), fill=AMBER)
    for x in range(20, 45, 3):
        u = (x - 20) / 24
        put(f, x, 30 - 14 * math.sin(math.pi * u), dim(AMBER, 0.3))
    t35c(f, 48, "DEPLOY", WHITE)
    t35c(f, 56, "TO EDGE", LIME)
    t35c(f, 6, "NO CLOUD", dim(CYAN, 0.6))
    return f


@p64("p_code_gitmerge", n=32, ms=80, note="two branch lanes, commits flow right and merge, '+ MERGE'")
def gitmerge(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)

    def lane_b(x):
        if x < 30:
            return 40
        if x > 46:
            return 22
        return 40 - 18 * (0.5 - 0.5 * math.cos(math.pi * (x - 30) / 16))
    d.line((0, 22, 63, 22), fill=dim(CYAN, 0.6))
    for x in range(0, 47):
        put(f, x, lane_b(x), dim(MAG, 0.6))
    for k in range(5):
        x = (k * 16 + t) % 80 - 8
        d.ellipse((x - 2, 20, x + 2, 24), fill=CYAN)
        xb = (k * 16 + 8 + t) % 80 - 8
        if xb <= 46:
            yb = lane_b(max(0, xb))
            d.ellipse((xb - 2, yb - 2, xb + 2, yb + 2), fill=MAG)
    d.ellipse((44, 19, 50, 25), outline=WHITE)
    t35(f, 2, 12, "MAIN", dim(CYAN, 0.8))
    t35(f, 2, 44, "FEATURE", dim(MAG, 0.8))
    unic(f, 46, "+ MERGE", grad(4, 56, [MAG, CYAN]))
    return f


@p64("p_code_grok", n=24, ms=80, note="2x 'GROK' inside a tilted ring with an orbiting particle")
def grok(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.ellipse((2, 18, 61, 46), outline=dim(WHITE, 0.35))
    unic(f, 16, "GROK", grad(0, 64, [WHITE, (180, 190, 210)]), 2)
    a = TAU * t / n
    x, y = 32 + 29.5 * math.cos(a), 32 + 14 * math.sin(a)
    front = math.sin(a) > 0
    for j in range(5):
        b = a - j * 0.12
        put(f, 32 + 29.5 * math.cos(b), 32 + 14 * math.sin(b), dim(CYAN, 1 - j / 5))
    if front:
        d.ellipse((x - 2, y - 2, x + 2, y + 2), fill=WHITE)
    else:
        put(f, x, y, CYAN)
    return f


def _tokstrip(seed, w, col):
    r = random.Random(seed)
    s = Image.new("RGB", (w, 4))
    d = ImageDraw.Draw(s)
    x = 0
    while x < w - 4:
        ln = 2 + r.randrange(7)
        ln = min(ln, w - 1 - x)
        d.rectangle((x, 0, x + ln - 1, 3), fill=dim(col, 0.5 + 0.5 * r.random()))
        x += ln + 2
    return s


@p64("p_code_llm", n=16, ms=60, note="'LOCAL AI' + six lanes of variable-width tokens streaming")
def llm(t, n):
    f = canvas()
    unic(f, 0, "LOCAL AI", grad(0, 64, [WHITE, CYAN, LIME]))
    for i in range(6):
        s = _tokstrip(30 + i, 64, [CYAN, LIME, WHITE][i % 3])
        sp = (1 + i % 3) * 4
        scroll(s, sp * t * 64 // (n * 4) if False else (sp * t) % 64, 0, 22 + 7 * i, 64, f)
    return f


TERM = ["> IDF.PY BUILD", "CC MAIN.C", "CC RTLSDR.C", "CC FFT.C", "LD FIRMWARE.ELF", "SIZE 1.2MB OK",
        "> PYTEST -Q", "424 PASSED", "> GIT PUSH", "MASTER OK", "> ORC STATUS", "SIGNAL -42DB", "READY"]


@p64("p_code_terminal", n=39, ms=100, note="terminal window: build log scrolls up, blinking prompt")
def terminal(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rectangle((0, 0, 63, 7), fill=(20, 40, 20))
    for i, c in enumerate((RED, AMBER, GREEN)):
        put(f, 2 + 3 * i, 3, c)
    t35(f, 22, 1, "ORC", dim(LIME, 0.8))
    L = len(TERM)
    off = 2 * t                                     # 6 px per line, 3 frames per line
    base = off // 6
    sub = off % 6
    for j in range(10):
        line = TERM[(base + j) % L]
        y = 9 + 6 * j - sub
        if 8 < y < 58:
            col = AMBER if line.startswith(">") else (GREEN if line.endswith("OK") or "PASSED" in line else dim(LIME, 0.75))
            t35(f, 1, y, line[:16], col)
    d.rectangle((0, 57, 63, 63), fill=BLACK)
    t35(f, 1, 58, ">", LIME)
    if (t // 3) % 2 == 0:
        d.rectangle((6, 58, 8, 62), fill=LIME)
    return f


@p64("p_code_tests_fail", n=16, ms=90, note="big red X blinking, '1 FAILED', red scanline")
def tests_fail(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    on = t % 8 < 5
    c = RED if on else dim(RED, 0.4)
    d.line((18, 6, 45, 33), fill=c, width=5)
    d.line((45, 6, 18, 33), fill=c, width=5)
    unic(f, 38, "1 FAILED", RED)
    y = int(t * 64 / n)
    for x in range(64):
        if f.getpixel((x, y)) == (0, 0, 0):
            put(f, x, y, (70, 0, 0))
    return f


def _checks():
    s = Image.new("RGB", (16, 10))
    d = ImageDraw.Draw(s)
    d.line([(2, 5), (5, 8), (11, 1)], fill=GREEN, width=2)
    return s


@p64("p_code_tests_pass", n=16, ms=80, note="'TESTS' / 'PASS' with a marquee of check marks")
def tests_pass(t, n):
    f = canvas()
    unic(f, 2, "TESTS", grad(12, 40, [WHITE, GREEN]))
    unic(f, 20, "PASS", GREEN)
    s = _checks()
    scroll(s, t, 0, 42, 64, f)
    scroll(s, -t + 8, 0, 53, 64, f)
    return f


# ====================================================================== hw
@p64("p_hw_antenna", n=16, ms=90, note="mast antenna radiating wavefronts both ways + 'RTL-SDR V4'")
def antenna(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.line((32, 50, 32, 12), fill=(180, 190, 200))
    d.line((26, 50, 32, 30), fill=(120, 130, 140))
    d.line((38, 50, 32, 30), fill=(120, 130, 140))
    d.line((27, 18, 37, 18), fill=(180, 190, 200))
    put(f, 32, 11, RED if t % 8 < 4 else dim(RED, 0.4))
    for k in range(3):
        r = ((t / n + k / 3) % 1) * 28 + 4
        c = dim(CYAN, 1 - r / 34)
        d.arc((32 - r, 14 - r, 32 + r, 14 + r), 140, 220, fill=c)
        d.arc((32 - r, 14 - r, 32 + r, 14 + r), -40, 40, fill=c)
    t35c(f, 56, "RTL-SDR V4", LIME)
    return f


@p64("p_hw_esp32p4", n=24, ms=60, note="big QFN chip 'ESP32 / P4' with LEDs chasing round its pins")
def esp32p4(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rectangle((14, 14, 49, 49), fill=(28, 30, 34), outline=(90, 95, 105))
    pins = []
    for i in range(8):
        p = 17 + 4 * i
        pins += [(p, 11, p, 13), ]
    for i in range(8):
        p = 17 + 4 * i
        pins.append((50, p, 52, p))
    for i in range(8):
        p = 46 - 4 * i
        pins.append((p, 50, p, 52))
    for i in range(8):
        p = 46 - 4 * i
        pins.append((11, p, 13, p))
    head = int(len(pins) * t / n)
    for i, (a, b, c, e) in enumerate(pins):
        dd = (head - i) % len(pins)
        col = WHITE if dd == 0 else (LIME if dd < 4 else (90, 95, 105))
        d.line((a, b, c, e), fill=col)
    unic(f, 16, "ESP32", (200, 210, 220)) if uw("ESP32") <= 34 else None
    t35c(f, 22, "ESP32", (210, 215, 225))
    unic(f, 28, "P4", LIME)
    put(f, 17, 17, CYAN)
    return f


PCB_PATHS = [[(0, 10), (20, 10), (26, 16), (63, 16)], [(0, 30), (12, 30), (18, 24), (40, 24), (46, 30), (63, 30)],
             [(10, 63), (10, 44), (16, 38), (63, 38)], [(30, 63), (30, 50), (36, 44), (63, 44)],
             [(0, 54), (20, 54), (24, 58), (63, 58)], [(50, 0), (50, 8), (56, 14), (56, 24)]]


def _seglen(p):
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(p, p[1:]))


def _along(p, s):
    for a, b in zip(p, p[1:]):
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        if s <= L:
            k = s / L if L else 0
            return a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k
        s -= L
    return p[-1]


@p64("p_hw_pcb", n=16, ms=80, note="PCB traces with vias and light pulses running along them")
def pcb(t, n):
    f = canvas((0, 14, 6))
    d = ImageDraw.Draw(f)
    for p in PCB_PATHS:
        d.line(p, fill=(30, 110, 40), width=1)
        for q in (p[0], p[-1]):
            ring(d, q[0], q[1], 1, (200, 170, 60))
    for i, p in enumerate(PCB_PATHS):
        L = _seglen(p)
        for k in range(2):
            s = ((t / n + k / 2 + i * 0.17) % 1) * L
            x, y = _along(p, s)
            put(f, x, y, WHITE)
            x2, y2 = _along(p, max(0, s - 2))
            put(f, x2, y2, LIME)
    d.rectangle((22, 3, 41, 8), fill=(20, 20, 24), outline=(90, 90, 100))
    return f


_TAB = {}


@p64("p_hw_tab5", n=16, ms=100, note="M5Stack Tab5 outline whose screen runs a looping waterfall")
def tab5(t, n):
    if "wf" not in _TAB:
        _TAB["wf"] = waterfall(48, 26, n, seed=5, q=4)
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rounded_rectangle((4, 8, 59, 45), radius=3, outline=(160, 165, 175))
    d.rectangle((7, 11, 56, 42), outline=(50, 52, 60))
    f.paste(_TAB["wf"](t), (8, 12))
    put(f, 31, 9, (90, 90, 100))
    t35c(f, 52, "TAB5 SDR", LIME)
    return f


def _uart_bits():
    bits = []
    for ch in "Orc":
        v = ord(ch)
        bits += [0] + [(v >> i) & 1 for i in range(8)] + [1]
    return bits + [1, 1]


@p64("p_hw_uart", n=32, ms=60, note="TX line square wave of 'Orc' 8N1 scrolling, bit labels below")
def uart(t, n):
    bits = _uart_bits()
    bw = 4
    s = Image.new("RGB", (len(bits) * bw, 30))
    d = ImageDraw.Draw(s)
    for i, b in enumerate(bits):
        y = 4 if b else 20
        d.line((i * bw, y, i * bw + bw - 1, y), fill=LIME)
        if i and bits[i - 1] != b:
            d.line((i * bw, 4, i * bw, 20), fill=LIME)
        t35(s, i * bw, 24, str(b), dim(CYAN, 0.6)) if False else put(s, i * bw + 1, 26, CYAN if b else DCYAN)
    f = canvas()
    sw = s.width
    off = (t * sw) // n
    scroll(s, off, 0, 16, 64, f)
    t35(f, 1, 2, "TX", AMBER)
    t35(f, 22, 2, "115200 8N1", dim(WHITE, 0.6))
    t35c(f, 54, "'ORC' 4F 72 63", dim(LIME, 0.8))
    return f


# ====================================================================== orc
def _marq(txt, col, step=2):
    w = uw(txt) + 24
    w += (-w) % step
    s = Image.new("RGB", (w, 16))
    uni(s, 0, 0, txt, col)
    return s


def _adv_strip():
    w = uw("THE WARCHIEF'S ADVISOR") + 16
    w += (-w) % 4
    s = Image.new("RGB", (w, 16))
    x = uni(s, 0, 0, "THE WARCHIEF'S ", LIME)      # two flat colours: ~20 % smaller GIF than a gradient
    uni(s, x, 0, "ADVISOR", CYAN)
    return s


_ADV = _adv_strip()


@p64("p_orc_advisor", n=None, ms=90, note="orc 3x seated, 'THE WARCHIEF'S ADVISOR' marquee below")
def advisor(t, n):
    f = canvas()
    orc(f, 8, 0, 3)
    scroll(_ADV, 4 * t, 0, 48, 64, f)
    return f


@p64("p_orc_banner", n=12, ms=110, note="war banner on a pole rippling in the wind with the orc emblem")
def banner(t, n):
    flag = Image.new("RGB", (44, 40), (120, 20, 15))
    ImageDraw.Draw(flag).rectangle((0, 0, 43, 39), outline=(200, 160, 40))
    orc(flag, 6, 4, 2)
    f = canvas()
    d = ImageDraw.Draw(f)
    d.line((8, 4, 8, 63), fill=(150, 110, 60))
    put(f, 8, 3, AMBER)
    for x in range(44):
        off = 3 * math.sin(TAU * (x / 22 - t / n)) * (x / 44)
        shade = 0.75 + 0.25 * math.cos(TAU * (x / 22 - t / n))
        col = flag.crop((x, 0, x + 1, 40))
        col = col.point(lambda v: int(v * shade))
        f.paste(col, (9 + x, int(round(6 + off))))
    return f


@p64("p_orc_drums", n=16, ms=90, note="two war drums pounding a 4-beat with impact rings, 'WAR DRUMS'")
def drums(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    beat = (t * 4 // n)
    ph = (t * 4 % n) / n * 4
    for i, cx in enumerate((18, 46)):
        hit = beat % 2 == i
        d.rectangle((cx - 11, 32, cx + 11, 48), fill=(110, 50, 20))
        for x in range(cx - 11, cx + 12, 4):
            d.line((x, 32, x + 2, 48), fill=(200, 160, 60))
        d.ellipse((cx - 11, 27, cx + 11, 37), fill=(220, 200, 160), outline=(110, 50, 20))
        lift = 0 if hit and ph < 0.5 else 8
        d.line((cx + 3, 26 - lift, cx + 12, 14 - lift), fill=(200, 150, 90), width=2)
        if hit:
            r = 4 + ph * 10
            d.ellipse((cx - r * 1.4, 32 - r * 0.5, cx + r * 1.4, 32 + r * 0.5), outline=dim(AMBER, 1 - ph))
    t35c(f, 56, "WAR DRUMS", AMBER)
    return f


@p64("p_orc_eyes", n=24, ms=90, note="orc face 3x, eyes glow brighter and blink once, 'WARCHIEF'")
def eyes(t, n):
    f = canvas()
    orc(f, 8, 0, 3)
    k = 0.5 + 0.5 * math.sin(TAU * t / n)
    blink = t in (n // 2, n // 2 + 1)
    for ex in (6, 9):
        x0, y0 = 8 + ex * 3, 7 * 3
        col = (40, 120, 10) if blink else mix((230, 255, 140), (255, 60, 30), k)
        ImageDraw.Draw(f).rectangle((x0, y0, x0 + 2, y0 + 2), fill=col)
        if not blink and k > 0.6:
            for dx, dy in ((-1, 1), (3, 1)):
                put(f, x0 + dx, y0 + dy, dim((255, 60, 30), k - 0.3))
    t35c(f, 54, "WARCHIEF", grad(16, 32, [LIME, AMBER]))
    return f


@p64("p_orc_forge", n=16, ms=80, note="hammer strikes an anvil, sparks burst, 'FORGE'")
def forge(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.polygon([(14, 34), (50, 34), (46, 40), (38, 40), (40, 48), (24, 48), (26, 40), (18, 40)], fill=(110, 115, 125))
    d.rectangle((20, 48, 44, 52), fill=(70, 72, 80))
    u = t / n
    ang = -1.1 + 1.1 * (1 - abs(math.cos(math.pi * u))) if False else (-1.2 if u < 0.5 else -1.2 + 1.2 * min(1, (u - 0.5) * 6))
    if u >= 0.5 + 1 / 6:
        ang = -1.2 * min(1, (u - 0.5 - 1 / 6) * 3)
    px, py = 54, 20
    hx, hy = px + 24 * math.cos(math.pi + ang), py + 24 * math.sin(math.pi + ang)
    d.line((px, py, hx, hy), fill=(150, 100, 60), width=2)
    d.rectangle((hx - 4, hy - 3, hx + 4, hy + 3), fill=(170, 175, 185))
    if 0.66 <= u < 0.95:
        k = (u - 0.66) / 0.3
        r = random.Random(4)
        for i in range(14):
            a = -math.pi * r.random()
            s = 4 + 18 * k * (0.5 + r.random())
            put(f, 32 + s * math.cos(a), 33 + s * math.sin(a) + 10 * k * k, mix(WHITE, AMBER, k))
    t35c(f, 57, "FORGE", AMBER)
    return f


@p64("p_orc_horn", n=16, ms=90, note="war horn blasting sound waves toward 'AROOO'")
def horn(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.polygon([(4, 30), (10, 26), (24, 20), (30, 14), (32, 34), (24, 30), (10, 34)], fill=(220, 200, 150))
    d.line((6, 32, 28, 22), fill=(150, 110, 60))
    d.ellipse((27, 13, 35, 35), outline=(250, 230, 180))
    for k in range(4):
        r = ((t / n + k / 4) % 1) * 30 + 4
        d.arc((31 - r, 24 - r, 31 + r, 24 + r), -45, 45, fill=dim(AMBER, 1 - r / 36))
    unic(f, 44, "AROOO", grad(10, 44, [AMBER, (255, 230, 150)]))
    return f


def _orcling():
    s = Image.new("RGB", (8, 14))
    d = ImageDraw.Draw(s)
    d.rectangle((2, 0, 5, 3), fill=LIME)
    d.rectangle((1, 4, 6, 9), fill=(40, 120, 10))
    d.line((7, 0, 7, 13), fill=(180, 180, 180))
    return s


@p64("p_orc_march", n=16, ms=100, note="two columns of tiny orcs with spears marching and wrapping")
def march(t, n):
    f = canvas()
    o = _orcling()
    for row, (y, sp, off) in enumerate(((10, 1, 0), (36, 1, 8))):
        for k in range(4):
            x = (k * 16 + off + sp * t) % 64
            step = (t + k) % 2
            f.paste(o, (x, y), o.convert("L").point(lambda v: 255 if v else 0))
            d = ImageDraw.Draw(f)
            d.line((x + 2 + step, y + 10, x + 1 + step, y + 13), fill=(40, 120, 10))
            d.line((x + 5 - step, y + 10, x + 6 - step, y + 13), fill=(40, 120, 10))
            if x > 56:
                f.paste(o, (x - 64, y), o.convert("L").point(lambda v: 255 if v else 0))
        ImageDraw.Draw(f).line((0, y + 14, 63, y + 14), fill=(60, 40, 20))
    t35c(f, 57, "WARBAND", AMBER)
    return f


def _runestrip(seed, w=64):
    r = random.Random(seed)
    s = Image.new("RGB", (w, 9))
    for i in range(w // 8):
        g = [r.random() < 0.5 for _ in range(20)]
        for k, on in enumerate(g):
            if on:
                put(s, i * 8 + 1 + k % 4, 1 + k // 4 + (k // 4), (255, 120, 30))
        put(s, i * 8 + 2, 0, (255, 200, 80))
    return s


@p64("p_orc_runes", n=32, ms=70, note="'ORCISH' with two glowing rune ribbons scrolling opposite ways")
def runes(t, n):
    f = canvas()
    unic(f, 0, "ORCISH", grad(10, 44, [AMBER, (255, 220, 120)]))
    a, b = _runestrip(1), _runestrip(2)
    scroll(a, 2 * t, 0, 22, 64, f)
    scroll(b, -2 * t, 0, 38, 64, f)
    glow = 0.5 + 0.5 * math.sin(TAU * t / n)
    for y in (20, 33, 36, 49):
        for x in range(0, 64, 2):
            put(f, x, y, dim((255, 120, 30), 0.25 + 0.25 * glow))
    t35c(f, 56, "TONGUE", dim(AMBER, 0.7))
    return f


def _flame(f, cx, cy, t, n, seed):
    for i in range(18):
        r = random.Random(seed * 50 + i)
        a = r.random()
        h = 6 + 8 * (0.5 + 0.5 * math.sin(TAU * (t / n * (1 + i % 3) + a)))
        x = cx - 3 + (i % 7)
        for y in range(int(h)):
            k = y / max(1, h)
            put(f, x, cy - y, mix((255, 230, 120), (200, 30, 0), k))


@p64("p_orc_torches", n=12, ms=100, note="two flickering torches framing 'WAR' / 'CHIEF'")
def torches(t, n):
    f = canvas((8, 4, 2))
    d = ImageDraw.Draw(f)
    for cx in (8, 55):
        d.rectangle((cx - 1, 30, cx + 1, 60), fill=(110, 70, 30))
        d.rectangle((cx - 3, 26, cx + 3, 30), fill=(90, 90, 100))
        _flame(f, cx, 25, t, n, cx)
    k = 0.8 + 0.2 * math.sin(TAU * t / n)
    unic(f, 12, "WAR", dim((255, 200, 120), k))
    unic(f, 32, "CHIEF", dim((255, 200, 120), k))
    return f


@p64("p_orc_tusks", n=16, ms=90, note="orc 3x with a gleam sweeping its tusks, 'STAY ORC' pulsing")
def tusks(t, n):
    f = canvas()
    orc(f, 8, 0, 3)
    gx = -8 + (t / n) * 80
    for (x, y) in ((2, 10), (2, 11), (3, 11), (2, 12), (3, 12), (13, 10), (12, 11), (13, 11), (12, 12), (13, 12)):
        X, Y = 8 + 3 * x, 3 * y
        if abs(X - gx) < 6:
            ImageDraw.Draw(f).rectangle((X, Y, X + 2, Y + 2), fill=WHITE)
    k = 0.6 + 0.4 * math.cos(TAU * t / n)
    unic(f, 47, "STAY ORC", dim(LIME, k))
    return f


# ====================================================================== proj
@p64("p_proj_beamforge", n=24, ms=80, note="DMX laser fan sweeping from a projector, 'BEAMFORGE'")
def beamforge(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    cx, cy = 32, 54
    base = math.sin(TAU * t / n) * 0.5
    cols = [MAG, CYAN, LIME, AMBER, RED]
    for i in range(7):
        a = -math.pi / 2 + base + (i - 3) * 0.22
        d.line((cx, cy, cx + 70 * math.cos(a), cy + 70 * math.sin(a)), fill=dim(cols[i % 5], 0.85))
    d.rectangle((cx - 6, cy, cx + 6, cy + 5), fill=(80, 80, 90))
    put(f, cx, cy, WHITE)
    d.rectangle((12, 0, 51, 8), fill=BLACK)
    t35c(f, 2, "BEAMFORGE", WHITE)
    return f


@p64("p_proj_esprtlsdr", n=16, ms=80, note="USB packets flow from an RTL dongle into an ESP32")
def esprtlsdr(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rounded_rectangle((2, 20, 22, 32), radius=2, fill=(30, 30, 36), outline=(150, 150, 160))
    t35(f, 5, 24, "RTL", CYAN)
    d.rectangle((23, 24, 27, 28), fill=(150, 150, 160))
    d.rectangle((40, 14, 61, 38), fill=(28, 30, 34), outline=LIME)
    t35(f, 43, 22, "ESP", LIME)
    t35(f, 43, 28, "32", LIME)
    for k in range(4):
        x = 28 + ((t / n + k / 4) % 1) * 11
        d.rectangle((x, 25, x + 1, 27), fill=AMBER)
    put(f, 6, 22, RED if t % 4 < 2 else (80, 0, 0))
    unic(f, 42, "ESP-RTL", grad(4, 56, [CYAN, LIME]))
    t35c(f, 58, "SDR", dim(LIME, 0.8))
    return f


@p64("p_proj_neondrive", n=16, ms=80, note="synthwave grid road rushing to a striped magenta sun, 'NEONDRIVE'")
def neondrive(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    hz = 34
    for y in range(12, hz):
        r = 12
        dy = y - (hz - 2)
        if dy * dy <= 13 * 13:
            w = int(math.sqrt(13 * 13 - dy * dy))
            if not (y > 24 and (y % 3 == 0)):
                d.line((32 - w, y, 32 + w, y), fill=mix((255, 220, 60), MAG, (y - 12) / 22))
        _ = r
    d.line((0, hz, 63, hz), fill=MAG)
    for i in range(-8, 9):
        d.line((32 + i * 3, hz, 32 + i * 16, 63), fill=dim(MAG, 0.7))
    for k in range(6):
        u = ((k + t / n) / 6)
        y = hz + (63 - hz) * u * u
        d.line((0, y, 63, y), fill=dim(CYAN, 0.4 + 0.6 * u))
    t35c(f, 2, "NEONDRIVE", grad(14, 36, [CYAN, MAG]))
    return f


@p64("p_proj_orclink", n=16, ms=90, note="chain links with a data pulse running through, 'ORCLINK / RUST EDGE'")
def orclink(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    for i in range(6):
        x = -6 + i * 13
        if i % 2 == 0:
            d.rounded_rectangle((x, 18, x + 16, 28), radius=5, outline=(170, 175, 190), width=2)
        else:
            d.line((x, 23, x + 16, 23), fill=(170, 175, 190), width=2)
    px = (t / n) * 80 - 8
    for j in range(6):
        put(f, px - j, 23, mix(AMBER, (255, 120, 0), j / 6))
        put(f, px - j, 22, dim(AMBER, 0.5 - j / 14))
    unic(f, 34, "ORCLINK", grad(4, 56, [ORANGE, AMBER]))
    t35c(f, 54, "RUST EDGE", dim(ORANGE, 0.8))
    return f


def _terrain(x, y):
    return (math.sin(x / 9) + math.sin(y / 7 + x / 23) + 0.6 * math.sin((x + y) / 5)) / 2.6


@p64("p_proj_orcmaps", n=32, ms=90, note="contour-line terrain scrolling under a bouncing map pin")
def orcmaps(t, n):
    f = canvas()
    px = f.load()
    off = 2 * t
    for y in range(12, 64):
        for x in range(64):
            v = _terrain((x + off) * TAU * 0 + (x + off) % 64 * 1.0 if False else ((x + off) % 64), y)
            band = int((v + 1) * 5)
            v2 = _terrain(((x + off + 1) % 64), y)
            if int((v2 + 1) * 5) != band or int((_terrain((x + off) % 64, y + 1) + 1) * 5) != band:
                px[x, y] = mix((30, 110, 40), (140, 220, 120), band / 10)
    bounce = abs(math.sin(TAU * 2 * t / n)) * 6
    cx, cy = 32, 32 - bounce
    d = ImageDraw.Draw(f)
    d.ellipse((cx - 5, cy - 12, cx + 5, cy - 2), fill=RED)
    d.polygon([(cx - 4, cy - 5), (cx + 4, cy - 5), (cx, cy + 3)], fill=RED)
    d.ellipse((cx - 2, cy - 9, cx + 2, cy - 5), fill=WHITE)
    t35c(f, 2, "ORCMAPS", LIME)
    return f


MESH = [(10, 20), (32, 12), (54, 22), (18, 44), (46, 46)]
LINKS = [(0, 1), (1, 2), (0, 3), (1, 3), (1, 4), (2, 4), (3, 4)]


@p64("p_proj_orcmesh", n=15, ms=100, note="five mesh nodes, packets hopping link to link, 'ORCMESH'")
def orcmesh(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    for a, b in LINKS:
        d.line((MESH[a], MESH[b]), fill=(25, 70, 40))
    hop = t // 3
    u = (t % 3) / 3
    path = [0, 1, 2, 4, 3, 0]                       # closed tour: the loop seam is a normal hop
    a, b = path[hop % 5], path[hop % 5 + 1]
    x = MESH[a][0] + (MESH[b][0] - MESH[a][0]) * u
    y = MESH[a][1] + (MESH[b][1] - MESH[a][1]) * u
    d.line((MESH[a], MESH[b]), fill=dim(CYAN, 0.6))
    for i, (nx, ny) in enumerate(MESH):
        on = i in (a, b)
        d.ellipse((nx - 3, ny - 3, nx + 3, ny + 3), fill=LIME if on else DLIME)
    d.ellipse((x - 1.5, y - 1.5, x + 1.5, y + 1.5), fill=WHITE)
    t35c(f, 56, "ORCMESH", LIME)
    return f


@p64("p_proj_theorc", n=24, ms=90, note="orc 2x + 'TheOrc' with a tusk-glint highlight sweeping the name")
def theorc(t, n):
    f = canvas()
    orc(f, 16, 0, 2)
    hx = -10 + (t / n) * 90
    uni(f, 8, 36, "TheOrc", lambda gx, gy: WHITE if abs(gx - hx - (gy - 44) * 0.5) < 2.5 else
        _lerp_stops([LIME, (120, 250, 150), CYAN], (gx - 8) / 47))
    return f


@p64("p_proj_visualizer", n=24, ms=80, note="16 retro audio bars bouncing in sync, 'VISUALIZER'")
def visualizer(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    r = random.Random(12)
    phs = [r.random() for _ in range(16)]
    for i in range(16):
        v = 0.25 + 0.75 * abs(math.sin(TAU * (t / n * (1 + i % 3) + phs[i])))
        h = int(v * 44)
        for s in range(0, h, 3):
            d.rectangle((i * 4, 52 - s, i * 4 + 2, 53 - s), fill=pal(s / 44, [LIME, AMBER, RED]))
    t35c(f, 58, "VISUALIZER", CYAN)
    return f


# ====================================================================== rf
@p64("p_rf_adsb", n=32, ms=120, note="full radar scope, sweep once per loop, blips flare as it passes")
def adsb(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    cx, cy = 32, 30
    for r in (9, 18, 27):
        ring(d, cx, cy, r, (10, 50, 20))
    d.line((cx - 28, cy, cx + 28, cy), fill=(10, 40, 16))
    d.line((cx, cy - 28, cx, cy + 28), fill=(10, 40, 16))
    a = TAU * t / n
    for j in range(8):
        b = a - j * 0.08
        d.line((cx, cy, cx + 27 * math.cos(b), cy + 27 * math.sin(b)), fill=dim(LIME, 1 - j / 8))
    for i, (ang, rr) in enumerate(((0.6, 20), (2.2, 12), (3.9, 24), (5.1, 16))):
        dd = (a - ang) % TAU
        k = max(0.2, 1 - dd / 3)
        x, y = cx + rr * math.cos(ang), cy + rr * math.sin(ang)
        d.rectangle((x - 1, y - 1, x + 1, y + 1), fill=dim(AMBER, k))
    t35c(f, 59, "ADS-B 1090", CYAN)
    return f


@p64("p_rf_airband", n=16, ms=100, note="plane crossing over an AM carrier with swelling sidebands")
def airband(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    x = (t / n) * 80 - 8
    d.polygon([(x, 12), (x - 10, 10), (x - 12, 6), (x - 9, 10), (x - 14, 12), (x - 9, 14), (x - 12, 18), (x - 10, 14)], fill=WHITE)
    k = 0.5 + 0.5 * math.sin(TAU * 2 * t / n)
    for i in range(64):
        dx = abs(i - 32)
        v = 0.12
        if dx == 0:
            v = 1.0
        elif 2 <= dx <= 14:
            v = 0.12 + 0.55 * k * math.exp(-((dx - 7) / 5) ** 2)
        h = int(v * 30)
        d.line((i, 54, i, 54 - h), fill=dim(CYAN, 0.5) if dx else LIME)
        put(f, i, 54 - h, CYAN if dx else WHITE)
    t35c(f, 58, "AIRBAND AM", AMBER)
    return f


@p64("p_rf_bandscan", n=25, ms=70, note="channel comb; scan marker steps and pauses on active channels")
def bandscan(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    active = {3: 0.9, 9: 0.6, 13: 0.8}
    seq = []
    for ch in range(16):
        seq += [ch] * (4 if ch in active else 1)
    seq = (seq * 3)[:n]
    ch = seq[t % len(seq)]
    for c in range(16):
        x = 2 + c * 4
        v = active.get(c, 0.12)
        h = int(v * 30)
        col = LIME if c in active else DLIME
        d.rectangle((x, 44 - h, x + 1, 44), fill=col if c != ch else WHITE)
    d.polygon([(2 + ch * 4 - 2, 48), (2 + ch * 4 + 3, 48), (2 + ch * 4 + 0.5, 46)], fill=AMBER)
    t35c(f, 2, "BAND SCAN", CYAN)
    fq = 462.5625 + ch * 0.025
    t35c(f, 54, f"{fq:.4f}", AMBER if ch in active else dim(WHITE, 0.6))
    return f


def _rds_strip():
    txt = "ORC FM  ROCK THE PNW  "
    w = uw(txt)
    w += (-w) % 4
    s = Image.new("RGB", (w, 16))
    uni(s, 0, 0, txt, grad(0, w, [CYAN, WHITE, AMBER]))
    return s


@p64("p_rf_fmrds", n=None, ms=70, note="FM stereo pilot bars + an RDS radiotext marquee")
def fmrds(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    unic(f, 0, "88.7", AMBER)
    t35(f, 44, 6, "FM", dim(AMBER, 0.7))
    for i in range(2):
        v = 0.6 + 0.3 * math.sin(TAU * (t / n * 3 + i * 0.3))
        d.rectangle((4, 20 + 8 * i, 4 + int(v * 50), 24 + 8 * i), fill=LIME)
        t35(f, 58, 20 + 8 * i, "LR"[i], dim(LIME, 0.7))
    t35(f, 2, 38, "RDS", dim(CYAN, 0.7))
    put(f, 16, 40, LIME if t % 8 < 4 else DLIME)
    scroll(_rds_strip(), 4 * t, 0, 46, 64, f)
    return f


@p64("p_rf_iq", n=24, ms=90, note="QPSK constellation rotating a quarter turn, I/Q traces below")
def iq(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    cx, cy = 32, 22
    d.line((cx - 20, cy, cx + 20, cy), fill=(30, 50, 40))
    d.line((cx, cy - 20, cx, cy + 20), fill=(30, 50, 40))
    a0 = (math.pi / 2) * t / n + math.pi / 4
    r = random.Random(1)
    for q in range(4):
        a = a0 + q * math.pi / 2
        x, y = cx + 13 * math.cos(a), cy + 13 * math.sin(a)
        for _ in range(9):
            put(f, x + r.gauss(0, 1.2), y + r.gauss(0, 1.2), dim(CYAN, 0.7))
        d.rectangle((x - 1, y - 1, x + 1, y + 1), fill=WHITE)
    for row, (col, ph) in enumerate(((LIME, 0), (AMBER, math.pi / 2))):
        for x in range(64):
            y = 48 + row * 9 + 3 * math.sin(TAU * (x / 21 - t / n) + ph)
            put(f, x, y, col)
    t35(f, 0, 44, "I", LIME)
    t35(f, 0, 53, "Q", AMBER)
    return f


@p64("p_rf_lora", n=32, ms=70, note="full-field up-chirp spectrogram, diagonal chirps wrapping")
def lora(t, n):
    f = canvas()
    px = f.load()
    for y in range(64):
        for x in range(64):
            if (x * 7 + y * 13) % 31 == 0:
                px[x, y] = (0, 20, 10)
    for y in range(64):
        tt = (y + 2 * t) % 64
        fx = (tt * 2) % 64
        for w, k in ((0, 1.0), (-1, 0.5), (1, 0.5)):
            x = (fx + w) % 64
            px[x, 63 - y] = mix(DLIME, LIME, k) if k < 1 else WHITE if tt % 32 == 0 else LIME
    d = ImageDraw.Draw(f)
    d.rectangle((0, 0, 30, 8), fill=BLACK)
    t35(f, 2, 2, "LORA SF7", CYAN)
    return f


@p64("p_rf_m5dial", n=32, ms=80, note="round VFO knob rocking with a readout that follows")
def m5dial(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    cx, cy = 32, 36
    rock = 0.9 * math.sin(TAU * t / n)
    ring(d, cx, cy, 22, (80, 85, 95), 2)
    ring(d, cx, cy, 16, (40, 44, 50))
    for i in range(24):
        a = TAU * i / 24 + rock
        put(f, cx + 19 * math.cos(a), cy + 19 * math.sin(a), (120, 125, 135))
    a = -math.pi / 2 + rock
    d.line((cx + 8 * math.cos(a), cy + 8 * math.sin(a), cx + 15 * math.cos(a), cy + 15 * math.sin(a)), fill=AMBER, width=2)
    fq = 14.074 + rock * 0.01
    t35c(f, 2, f"{fq:.3f} MHZ", LIME)
    t35c(f, 33, "VFO", dim(WHITE, 0.6))
    return f


@p64("p_rf_multirate", n=32, ms=70, note="three sample streams at 1x, 1/2, 1/4 rate with DEC stages")
def multirate(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    for row, (step, col, lab) in enumerate(((2, CYAN, "1X"), (4, LIME, "/2"), (8, AMBER, "/4"))):
        y = 14 + row * 16
        t35(f, 0, y - 2, lab, col)
        for x in range(12, 64):
            if (x - t) % step == 0:
                v = math.sin(TAU * (x - t) / 32)
                d.line((x, y, x, y - int(5 * v)), fill=dim(col, 0.6))
                put(f, x, y - int(5 * v), col)
        if row < 2:
            t35(f, 26, y + 6, "DEC", dim(WHITE, 0.45))
    t35c(f, 58, "DSP MULTIRATE", dim(WHITE, 0.7))
    return f


@p64("p_rf_p25", n=24, ms=100, note="2x 'P25' + control channel and voice channels lighting in rotation")
def p25(t, n):
    f = canvas()
    unic(f, 0, "P25", grad(8, 48, [CYAN, WHITE]), 2)
    d = ImageDraw.Draw(f)
    cc = t % 2 == 0
    d.rectangle((2, 36, 14, 44), fill=AMBER if cc else DLIME if False else (120, 70, 0))
    t35(f, 4, 38, "CC", BLACK)
    act = (t // 6) % 4
    for i in range(4):
        x = 18 + i * 11
        on = i == act
        d.rectangle((x, 36, x + 8, 44), fill=LIME if on else (20, 50, 20))
        t35(f, x + 3, 38, str(i + 1), BLACK if on else DLIME)
    t35c(f, 52, f"TG 1{act}07", LIME)
    t35c(f, 58, "TRUNKING", dim(WHITE, 0.6))
    return f


@p64("p_rf_smeter", n=24, ms=90, note="cream analog S-meter face, needle swings S3..S9+20")
def smeter(t, n):
    f = canvas()
    d = ImageDraw.Draw(f)
    d.rounded_rectangle((1, 4, 62, 50), radius=4, fill=(230, 215, 170))
    cx, cy, r = 32, 56, 40
    for i in range(13):
        a = math.pi * (0.8 - 0.6 * i / 12)
        x0, y0 = cx + (r - 4) * math.cos(a), cy - (r - 4) * math.sin(a)
        x1, y1 = cx + r * math.cos(a), cy - r * math.sin(a)
        d.line((x0, y0, x1, y1), fill=(30, 30, 30) if i < 9 else (200, 30, 20))
    d.arc((cx - r, cy - r, cx + r, cy + r), 216, 324, fill=(30, 30, 30))
    v = 0.25 + 0.6 * (0.5 - 0.5 * math.cos(TAU * t / n)) + 0.08 * math.sin(TAU * 4 * t / n)
    a = math.pi * (0.8 - 0.6 * max(0, min(1, v)))
    d.line((cx, cy - 6, cx + (r - 2) * math.cos(a), cy - (r - 2) * math.sin(a)), fill=(200, 20, 10))
    t35(f, 4, 42, "S", (30, 30, 30))
    s = 1 + int(v * 12)
    lab = f"S{min(9, s)}" if s <= 9 else f"S9+{(s - 9) * 10}"
    t35c(f, 56, lab, AMBER)
    return f


def setup():
    from p64lib import REGISTRY
    REGISTRY["p_orc_advisor"]["n"] = _ADV.width // 4
    REGISTRY["p_rf_fmrds"]["n"] = _rds_strip().width // 4
