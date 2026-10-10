"""Generate 10 OrcSDR style presets (presets/orcsdr_style01..10) + a labeled contact sheet.

    python tools/make_styles.py

Brand palette: lime #8cfc28, cyan #5adcff / white-cyan, black. All presets are
stored upright; SignClient applies rotate_180 when sending.
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
from make_wave_small import FONT4x6, WORD_STOPS, BAR_STOPS, orc_icon, wordmark  # noqa: E402
from make_assets import spectrum_from_banner  # noqa: E402
from orcpixels.render import _lerp_stops, encode_gif, gradient_text  # noqa: E402

OUT = ROOT / "presets"
W, H = 64, 16
LIME, CYAN, WHITE = (140, 252, 40), (90, 220, 255), (225, 255, 255)
# waterfall color map: black -> deep green -> lime -> cyan -> white
WF_MAP = [(0, 0, 0), (0, 40, 12), (20, 130, 40), (140, 252, 40), (90, 220, 255), (235, 255, 255)]
ICE_MAP = [(0, 0, 0), (0, 25, 40), (0, 90, 120), (90, 220, 255), (170, 255, 200), (235, 255, 255)]
BANNER_LEVELS = None


def levels(n: int) -> list[float]:
    global BANNER_LEVELS
    if BANNER_LEVELS is None:
        raw = spectrum_from_banner(Image.open(ROOT / "assets/projects/orcsdr_banner.png"), 64)
        pk = max(raw) or 1
        BANNER_LEVELS = [0.08 + 0.92 * v / pk for v in raw]
    return [BANNER_LEVELS[int(i * 64 / n)] for i in range(n)]


def cmap(v: float, stops=WF_MAP):
    return _lerp_stops(stops, max(0.0, min(1.0, v)))


def text_mask(text="OrcSDR", sx=1, sy=1, font=FONT4x6) -> list[list[int]]:
    gw, gh = 4, 6
    w = (len(text) * (gw + 1) - 1) * sx
    rows = [[0] * w for _ in range(gh * sy)]
    for i, ch in enumerate(text):
        for y, r in enumerate(font[ch]):
            for x, c in enumerate(r):
                if c == "#":
                    for dy in range(sy):
                        for dx in range(sx):
                            rows[y * sy + dy][(i * (gw + 1) + x) * sx + dx] = 1
    return rows


class Waterfall:
    """Rolling spectrogram with a noise floor and a few drifting carriers."""

    def __init__(self, width, height, seed=1, carriers=((0.72, 0.9, 0.0), (0.3, 0.45, 0.004), (0.55, 0.35, -0.003))):
        self.w, self.h, self.rnd = width, height, random.Random(seed)
        self.carriers = [list(c) for c in carriers]   # pos (0-1), amplitude, drift/row
        self.rows = [self.line() for _ in range(height)]
        self.t = 0

    def line(self, paint=None, paint_off=0, paint_amp=0.95):
        r = self.rnd
        out = []
        for x in range(self.w):
            v = r.random() * 0.22                            # noise floor
            for pos, amp, _ in self.carriers:
                d = abs(x - pos * (self.w - 1))
                v += amp * math.exp(-(d * d) / 1.6) * (0.75 + 0.25 * r.random())
            if paint is not None:
                px = x - paint_off
                if 0 <= px < len(paint) and paint[px]:
                    v = max(v, paint_amp * (0.85 + 0.15 * r.random()))
            out.append(min(1.0, v))
        for c in self.carriers:
            c[0] = (c[0] + c[2]) % 1.0
        return out

    def push(self, row, down=True):
        if down:
            self.rows = [row] + self.rows[:-1]
        else:
            self.rows = self.rows[1:] + [row]

    def image(self, stops=WF_MAP, q=8) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h))
        for y, row in enumerate(self.rows):
            for x, v in enumerate(row):
                img.putpixel((x, y), cmap(round(v * q) / q, stops))   # quantized: smaller GIF
        return img


class LoopWaterfall:
    """Seamless waterfall: a precomputed history of N rows that wraps.

    Row k's noise comes from its own seeded RNG (tiled with period N) and carrier
    positions move as pos + drift * sin(2*pi*cycles*k/N), so row N == row 0 and
    scrolling N frames lands exactly on frame 0. Optional painted rows (text
    masks) are part of the history.
    """

    def __init__(self, width, height, n, seed=1, carriers=((0.72, 0.9, 0.0, 1), (0.3, 0.45, 0.04, 1),
                                                          (0.55, 0.35, 0.03, 2)), paint=None, paint_off=0):
        self.w, self.h, self.n = width, height, n
        paint = paint or {}
        self.hist = []
        for k in range(n):
            r = random.Random(seed * 100003 + k)
            row = []
            cpos = [(p + dr * math.sin(2 * math.pi * cy * k / n), a) for p, a, dr, cy in carriers]
            flick = [0.75 + 0.25 * r.random() for _ in cpos]
            for x in range(width):
                v = r.random() * 0.22
                for (pos, amp), fk in zip(cpos, flick):
                    d = abs(x - (pos % 1.0) * (width - 1))
                    v += amp * math.exp(-(d * d) / 1.6) * fk
                pr = paint.get(k)
                if pr is not None:
                    px = x - paint_off
                    if 0 <= px < len(pr) and pr[px]:
                        v = max(v, 0.95 * (0.85 + 0.15 * r.random()))
                row.append(min(1.0, v))
            self.hist.append(row)

    def rows_at(self, t, down=True):
        """Rows shown at frame t. down: newest (hist[t]) at top; up: newest at bottom."""
        if down:
            return [self.hist[(t - y) % self.n] for y in range(self.h)]
        return [self.hist[(t - (self.h - 1 - y)) % self.n] for y in range(self.h)]

    def image(self, t, down=True, stops=WF_MAP, q=8) -> Image.Image:
        img = Image.new("RGB", (self.w, self.h))
        for y, row in enumerate(self.rows_at(t, down)):
            for x, v in enumerate(row):
                img.putpixel((x, y), cmap(round(v * q) / q, stops))
        return img


def spectrum_img(lv, width, height, stops=BAR_STOPS, every=2, peaks=None) -> Image.Image:
    img = Image.new("RGB", (width, height))
    d = ImageDraw.Draw(img)
    for x in range(0, width, every):
        v = lv[min(len(lv) - 1, x * len(lv) // width)]
        h = max(1, round(v * height))
        c = _lerp_stops(stops, x / max(1, width - 1))
        d.line((x, height - 1, x, height - h), fill=tuple(int(k * 0.5) for k in c))
        d.point((x, height - h), fill=c)
        if peaks is not None:
            ph = max(1, round(peaks[x // every] * height))
            if ph > h:
                d.point((x, height - ph), fill=WHITE)
    return img


def jitter(lv, rnd, amt=0.12):
    return [max(0.04, min(1.0, v + rnd.uniform(-amt, amt) * (0.4 + v))) for v in lv]


def big_font():
    return ImageFont.truetype(str(ROOT / "orcpixels/fonts/unifont.otf"), 16)


# ------------------------------------------------------------------ styles
def s01_waterfall_word():
    """'OrcSDR' painted as signal energy into a scrolling-down waterfall (spectrogram painting)."""
    mask = text_mask(sx=2, sy=2)                 # 58 x 12
    off = (W - len(mask[0])) // 2
    n = 34
    paint = {4 + i: row for i, row in enumerate(reversed(mask))}   # bottom text row enters first
    wf = LoopWaterfall(W, H, n, seed=11, carriers=((0.85, 0.5, 0.0, 1), (0.12, 0.35, 0.0, 1)),
                       paint=paint, paint_off=off)
    frames = [wf.image(t) for t in range(n)]
    durs = [90] * n
    durs[4 + 12 + 1] = 1500                       # hold mid-loop while the word is centered
    return frames, durs, {"font": "4x6 scaled 2x2", "scroll": "down", "map": "WF_MAP", "hold_ms": 1500,
                          "loop": "34-row wrapping history"}


def s02_icon_waterfall():
    """Orc icon + small 4x6 wordmark + 9-row waterfall with drifting carriers."""
    n = 20
    wf = LoopWaterfall(47, 9, n, seed=2)
    frames = []
    wm = wordmark()
    for t in range(n):
        f = Image.new("RGB", (W, H)); f.paste(orc_icon(), (0, 0)); f.paste(wm, (18, 0)); f.paste(wf.image(t), (17, 7))
        frames.append(f)
    return frames, [110] * n, {"icon": True, "wordmark_x": 18, "waterfall": "47x9 rows 7-15",
                               "loop": "20-row wrapping history"}


def s03_spectrum_peakhold():
    """Orc icon + full-height animated spectrum with white peak-hold dots that decay."""
    n = 24
    rnd = random.Random(3)
    base = levels(47)
    # periodic jitter: each bar wiggles with an integer number of cycles per loop
    ph = [(rnd.uniform(0, 2 * math.pi), rnd.choice([1, 2, 3]), rnd.uniform(0, 2 * math.pi)) for _ in base]
    def lv_at(t):
        return [max(0.04, min(1.0, v + 0.18 * (0.4 + v) * 0.5 * (math.sin(2 * math.pi * c * t / n + a)
                                                               + math.sin(2 * math.pi * 2 * c * t / n + b))))
                for v, (a, c, b) in zip(base, ph)]
    peaks = [0.0] * 24
    frames = []
    for t in range(2 * n):                        # warm up one loop so peak-hold state is periodic
        lv = lv_at(t)
        cols = [lv[x] for x in range(0, 47, 2)]
        peaks = [max(p - 0.05, c) for p, c in zip(peaks, cols)]
        if t >= n:
            f = Image.new("RGB", (W, H)); f.paste(orc_icon(), (0, 0))
            f.paste(spectrum_img(lv, 47, 16, peaks=peaks), (17, 0))
            frames.append(f)
    return frames, [90] * n, {"peak_decay_per_frame": 0.05, "jitter": "periodic sines, 1-3 cycles/loop",
                              "loop": "2nd pass after warm-up (peak lookback 20 < 24 frames)"}


def s04_scan_sweep():
    """4x6 wordmark + spectrum with a lime scan cursor sweeping; bars light up as it passes."""
    base = levels(64)
    frames = []
    wm = wordmark()
    for i, cx in enumerate(range(0, 64, 4)):
        f = Image.new("RGB", (W, H))
        spec = spectrum_img(base, 64, 9, stops=[(20, 90, 40), (30, 110, 90)])
        d = ImageDraw.Draw(spec)
        for k in range(0, 7, 2):                   # trail wraps around the right edge
            x = (cx - k) % 64
            if x % 2:
                continue
            v = base[x]; h = max(1, round(v * 9))
            d.line((x, 8, x, 9 - h), fill=CYAN if k < 3 else LIME)
        d.line((cx, 0, cx, 8), fill=WHITE)
        f.paste(spec, (0, 7))
        f.paste(wm, (1, 0))
        frames.append(f)
    return frames, [80] * len(frames), {"cursor_step_px": 4}


def s05_big_pulse():
    """Big 16px Unifont 'OrcSDR' (cyan->lime gradient) with a slow brightness pulse."""
    txt = gradient_text("OrcSDR", 48, [WHITE, CYAN, (110, 250, 140), (190, 252, 50)])
    frames = []
    for i in range(12):
        k = 0.45 + 0.55 * (0.5 + 0.5 * math.cos(2 * math.pi * i / 12))
        f = Image.new("RGB", (W, H))
        f.paste(txt.point(lambda p: int(p * k)), (8, 0))
        frames.append(f)
    return frames, [110] * 12, {"font": "Unifont 16px", "pulse": "cos, 45-100%, 12 frames"}


def s06_matrix():
    """Matrix-style green rain with 'OrcSDR' (4x6, cyan-white) resolving in the middle."""
    n, period = 22, 22                            # drop paths wrap every 22 rows; speeds 1-2 divide n*speed
    rnd = random.Random(6)
    drops = [(rnd.randrange(period), rnd.choice([1, 1, 2])) for _ in range(W)]
    mask = text_mask()
    ox, oy = (W - 29) // 2, 5
    frames = []
    for t in range(n):
        f = Image.new("RGB", (W, H)); px = f.load()
        for x, (y0, sp) in enumerate(drops):
            y = (y0 + sp * t) % period             # head row in 0..21 (16..21 off-screen)
            for k in range(6):
                yy = y - k
                if 0 <= yy < H:
                    px[x, yy] = (200, 255, 200) if k == 0 else (0, max(40, 200 - k * 35), 0)
        # reveal pulses 0 -> 1 -> 0 once per loop (fully resolved for the middle ~half)
        reveal = min(1.0, 1.8 * (0.5 - 0.5 * math.cos(2 * math.pi * t / n)))
        for y, r in enumerate(mask):
            for x, c in enumerate(r):
                if c:
                    px[ox + x, oy + y] = _lerp_stops([(0, 90, 0), WHITE if x < 12 else CYAN], reveal)
                elif reveal > 0.5:
                    px[ox + x, oy + y] = (0, 0, 0)
        frames.append(f)
    return frames, [90] * n, {"rain_tail": 6, "rain_period_rows": period,
                              "reveal": "1.8*(0.5-0.5cos) clamped, 1 cycle/loop"}


def s07_waterfall_word_up():
    """'OrcSDR' painted into an ICE-colored waterfall scrolling UP, full width."""
    mask = text_mask(sx=2, sy=2)
    off = (W - len(mask[0])) // 2
    n = 34
    paint = {4 + i: row for i, row in enumerate(mask)}            # scrolling up: top text row first
    wf = LoopWaterfall(W, H, n, seed=7, carriers=((0.2, 0.4, 0.03, 1), (0.9, 0.55, 0.03, 1)),
                       paint=paint, paint_off=off)
    frames = [wf.image(t, down=False, stops=ICE_MAP) for t in range(n)]
    durs = [90] * n
    durs[4 + 12 + 1] = 1500
    return frames, durs, {"font": "4x6 scaled 2x2", "scroll": "up", "map": "ICE_MAP",
                          "loop": "34-row wrapping history"}


def s08_scope():
    """Retro oscilloscope: dotted grid + lime AM-modulated trace, wordmark top-left."""
    frames = []
    wm = wordmark()
    for i in range(16):
        f = Image.new("RGB", (W, H)); d = ImageDraw.Draw(f)
        for x in range(0, W, 8):
            for y in range(7, H, 2):
                d.point((x, y), fill=(0, 45, 20))
        pts = []
        for x in range(W):
            env = 0.6 + 0.4 * math.sin(2 * math.pi * (x / 64 + i / 16))
            y = 11.5 + 3.8 * env * math.sin(2 * math.pi * (x / 7 + i / 8))
            pts.append((x, y))
        d.line(pts, fill=LIME)
        d.point([(x, round(y)) for x, y in pts[::5]], fill=CYAN)
        f.paste(wm, (35, 0))
        frames.append(f)
    return frames, [80] * 16, {"trace": "AM sine, carrier period 7px", "grid": "8px dotted"}


def s09_smeter():
    """Orc icon + wordmark + segmented S-meter bar that bounces (lime / cyan / white top segments)."""
    n = 24
    frames = []
    wm = wordmark()
    for t in range(n):
        a = 2 * math.pi * t / n
        v = max(0.15, min(1.0, 0.55 + 0.30 * math.sin(a) + 0.14 * math.sin(2 * a + 1.3) + 0.06 * math.sin(4 * a + 0.4)))
        f = Image.new("RGB", (W, H)); f.paste(orc_icon(), (0, 0)); f.paste(wm, (18, 0))
        d = ImageDraw.Draw(f)
        segs = 11
        for s_ in range(segs):
            x = 18 + s_ * 4
            on = s_ < round(v * segs)
            c = LIME if s_ < 7 else CYAN if s_ < 9 else WHITE
            d.rectangle((x, 9, x + 2, 14), fill=c if on else (0, 35, 10))
        frames.append(f)
    return frames, [100] * n, {"segments": 11, "colors": "lime S1-7, cyan S8-9, white +dB",
                               "level": "sum of sines with 1/2/4 cycles per loop"}


def s10_pan_waterfall():
    """Classic SDR panadapter: spectrum (rows 0-6) over waterfall (rows 8-15), orc icon left."""
    n = 20
    wf = LoopWaterfall(47, 8, n, seed=10, carriers=((0.72, 0.9, 0.0, 1), (0.3, 0.45, 0.05, 1)))
    frames = []
    for t in range(n):
        row = wf.hist[t % n]                      # spectrum = newest waterfall line
        f = Image.new("RGB", (W, H)); f.paste(orc_icon(), (0, 0))
        f.paste(spectrum_img([max(0.05, v) for v in row], 47, 7, every=1), (17, 0))
        ImageDraw.Draw(f).line((17, 7, 63, 7), fill=(0, 60, 30))
        f.paste(wf.image(t), (17, 8))
        frames.append(f)
    return frames, [110] * n, {"spectrum": "rows 0-6, from latest waterfall line", "waterfall": "rows 8-15",
                               "loop": "20-row wrapping history; carrier drift 1 sine cycle/loop"}


STYLES = [
    ("orcsdr_style01", "Waterfall word (down)", s01_waterfall_word),
    ("orcsdr_style02", "Icon + wordmark + waterfall", s02_icon_waterfall),
    ("orcsdr_style03", "Spectrum with peak-hold", s03_spectrum_peakhold),
    ("orcsdr_style04", "Scan cursor sweep", s04_scan_sweep),
    ("orcsdr_style05", "Big wordmark pulse", s05_big_pulse),
    ("orcsdr_style06", "Matrix rain reveal", s06_matrix),
    ("orcsdr_style07", "Ice waterfall word (up)", s07_waterfall_word_up),
    ("orcsdr_style08", "Retro scope trace", s08_scope),
    ("orcsdr_style09", "S-meter bounce", s09_smeter),
    ("orcsdr_style10", "Panadapter + waterfall", s10_pan_waterfall),
]


def main():
    sheet_rows = []
    for name, title, fn in STYLES:
        frames, durs, params = fn()
        gif = encode_gif(frames, durs)
        (OUT / f"{name}.gif").write_bytes(gif)
        # representative frame: the held/centered one if any, else the middle
        if max(durs) > min(durs):
            rep = frames[durs.index(max(durs))]
        else:                                     # brightest frame
            rep = max(frames, key=lambda f: sum(f.convert("L").tobytes()))
        rep.save(OUT / f"{name}.png")
        big = rep.resize((640, 160), Image.Resampling.NEAREST)
        big.save(OUT / f"{name}_preview_10x.png")
        meta = {"name": name, "kind": "gif", "file": f"{name}.gif", "still": f"{name}.png",
                "preview": f"{name}_preview_10x.png", "project": "OrcSDR", "title": title,
                "description": f"{title}: {fn.__doc__.strip()}", "frames": len(frames),
                "frame_ms": durs if len(set(durs)) > 1 else durs[0], "bytes": len(gif),
                "generator": f"tools/make_styles.py::{fn.__name__}", "params": params}
        (OUT / f"{name}.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        sheet_rows.append((name, title, big, len(frames), len(gif)))
        print(f"{name}: {title:30} {len(frames):3} frames {len(gif):6} B")
    # labeled contact sheet
    lab = ImageFont.truetype(str(ROOT / "orcpixels/fonts/unifont.otf"), 16)
    pad, lh = 12, 22
    sheet = Image.new("RGB", (640 + 2 * pad, len(sheet_rows) * (160 + lh + pad) + pad), (28, 28, 28))
    d = ImageDraw.Draw(sheet)
    y = pad
    for name, title, big, n, b in sheet_rows:
        d.text((pad, y), f"{name}  {title}  ({n} frames, {b / 1024:.1f} KB)", fill=(200, 255, 170), font=lab)
        sheet.paste(big, (pad, y + lh))
        y += 160 + lh + pad
    sheet.save(OUT / "orcsdr_styles_sheet.png")
    print("wrote presets/orcsdr_styles_sheet.png")


if __name__ == "__main__":
    main()
