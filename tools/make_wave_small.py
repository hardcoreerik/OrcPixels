"""Build presets/orcsdr_wave_small.png: banner-faithful 64x16 OrcSDR frame.

Layout (upright, as viewed):
  x 0-15   orc face (orcsdr_16 pixel map) with a dim octagon-frame hint
  x 17-63  rows 0-4: 'OrcSDR' in a 3x5 pixel font, cyan-white -> lime gradient
           rows 6-15: thin spectrum bars (every other column), heights sampled
           from assets/projects/orcsdr_banner.png (low noise, bumps, tall peak
           right of center), green->cyan with bright tips
Also writes an 8-frame subtly animated GIF variant (orcsdr_wave_small_anim.gif).
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from make_assets import pixmap, spectrum_from_banner  # noqa: E402
from orcpixels.render import _lerp_stops, encode_gif  # noqa: E402

FONT3x5 = {
    "O": [".#.", "#.#", "#.#", "#.#", ".#."],
    "r": ["...", "...", "###", "#..", "#.."],
    "c": ["...", "...", "###", "#..", "###"],
    "S": [".##", "#..", ".#.", "..#", "##."],
    "D": ["##.", "#.#", "#.#", "#.#", "##."],
    "R": ["##.", "#.#", "##.", "#.#", "#.#"],
}
# v2 wordmark font (user asked for bigger than 3x5): 4 wide x 6 tall, 1px spacing
FONT4x6 = {
    "O": [".##.", "#..#", "#..#", "#..#", "#..#", ".##."],
    "r": ["....", "....", "#.##", "##..", "#...", "#..."],
    "c": ["....", "....", ".###", "#...", "#...", ".###"],
    "S": [".###", "#...", ".##.", "...#", "...#", "###."],
    "D": ["###.", "#..#", "#..#", "#..#", "#..#", "###."],
    "R": ["###.", "#..#", "###.", "#.#.", "#..#", "#..#"],
}
# banner wordmark: white-cyan 'Orc' -> green -> lime/yellow 'SDR'
WORD_STOPS = [(225, 255, 255), (120, 240, 255), (110, 250, 140), (190, 252, 50)]
# banner spectrum: green on the left/body, cyan toward tips/right
BAR_STOPS = [(40, 200, 90), (60, 240, 130), (70, 230, 210), (90, 220, 255)]
OCTAGON = (25, 110, 25)


def orc_icon() -> Image.Image:
    ic = pixmap(face=(140, 252, 40), shadow=(40, 120, 10), glint=(230, 255, 140))
    d = ImageDraw.Draw(ic)
    hint = Image.new("L", (16, 16))
    hd = ImageDraw.Draw(hint)
    for seg in [((0, 3), (3, 0)), ((12, 0), (15, 3)), ((15, 12), (12, 15)), ((3, 15), (0, 12))]:
        hd.line(seg, fill=255)
    for y in range(16):
        for x in range(16):
            if hint.getpixel((x, y)) and ic.getpixel((x, y)) == (0, 0, 0):
                d.point((x, y), fill=OCTAGON)
    return ic


def wordmark(text: str = "OrcSDR", font=FONT4x6) -> Image.Image:
    gw, gh = len(next(iter(font.values()))[0]), len(next(iter(font.values())))
    w = len(text) * (gw + 1) - 1
    img = Image.new("RGB", (w, gh))
    for i, ch in enumerate(text):
        for y, row in enumerate(font[ch]):
            for x, c in enumerate(row):
                if c == "#":
                    gx = i * (gw + 1) + x
                    img.putpixel((gx, y), _lerp_stops(WORD_STOPS, gx / (w - 1)))
    return img


def spectrum(levels, width=47, height=10, seed=None) -> Image.Image:
    rnd = random.Random(seed)
    img = Image.new("RGB", (width, height))
    d = ImageDraw.Draw(img)
    for x in range(0, width, 2):                 # thin bars with dark gaps
        v = levels[min(len(levels) - 1, x * len(levels) // width)]
        if seed is not None:
            v = max(0.05, min(1.0, v + rnd.uniform(-0.07, 0.07)))
        h = max(1, round(v * height))
        c = _lerp_stops(BAR_STOPS, x / (width - 1))
        body = tuple(int(k * 0.5) for k in c)
        d.line((x, height - 1, x, height - h), fill=body)
        tip = tuple(min(255, int(k * 1.1)) for k in c)
        d.point((x, height - h), fill=tip)
    return img


def frame(levels, seed=None, wm_x: int = 18) -> Image.Image:
    img = Image.new("RGB", (64, 16))
    img.paste(orc_icon(), (0, 0))
    wm = wordmark()                       # 4x6 font -> 29x6 px, rows 0-5
    img.paste(wm, (wm_x, 0))              # left-aligned right after the 16px icon (17 = 1px gap, 16 = flush)
    sy = wm.height + 1                    # 1 blank row, spectrum rows 7-15 (9 rows)
    img.paste(spectrum(levels, height=16 - sy, seed=seed), (17, sy))
    return img


def main():
    banner = Image.open(ROOT / "assets/projects/orcsdr_banner.png").convert("RGB")
    raw = spectrum_from_banner(banner, 47)
    peak = max(raw) or 1.0
    levels = [0.10 + 0.90 * v / peak for v in raw]
    out = ROOT / "presets"
    args = sys.argv[1:]
    build_still = "--anim-only" not in args
    build_anim = "--anim" in args or "--anim-only" in args
    wm_x = int(args[args.index("--wordmark-x") + 1]) if "--wordmark-x" in args else 18
    still = frame(levels, wm_x=wm_x)
    if not build_still:
        return _anim(out, levels, still, wm_x)
    still.save(out / "orcsdr_wave_small.png")
    still.resize((640, 160), Image.Resampling.NEAREST).save(out / "orcsdr_wave_small_preview_10x.png")
    meta = {"name": "orcsdr_wave_small", "kind": "image", "file": "orcsdr_wave_small.png",
            "preview": "orcsdr_wave_small_preview_10x.png", "project": "OrcSDR",
            "version": 2, "previous": "orcsdr_wave_small_v1",
            "description": f"Banner-faithful OrcSDR frame v2: orc icon + octagon hint, 4x6 gradient "
                           f"wordmark at x={wm_x} (after the icon), 9-row thin-bar spectrum sampled from orcsdr_banner.png",
            "generator": f"tools/make_wave_small.py --anim --wordmark-x {wm_x}",
            "params": {"icon": "orcsdr_16 pixel map, face #8cfc28, octagon #196e19",
                       "wordmark": {"font": "4x6 FONT4x6 (1px spacing, 29x6 px)", "stops": WORD_STOPS,
                                    "x": wm_x, "pos": f"x{wm_x}-{wm_x + 28}, rows 0-5"},
                       "spectrum": {"rows": "7-15", "bar_every": 2, "stops": BAR_STOPS,
                                    "levels": [round(v, 3) for v in levels]}},
            "animated_variant": "orcsdr_wave_small_anim.gif"}
    (out / "orcsdr_wave_small.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print("wrote presets/orcsdr_wave_small.*")
    if build_anim:
        _anim(out, levels, still, wm_x)


def _anim(out, levels, still, wm_x):
    frames = [still] + [frame(levels, seed=i, wm_x=wm_x) for i in range(1, 8)]
    (out / "orcsdr_wave_small_anim.gif").write_bytes(encode_gif(frames, 150))
    still.resize((640, 160), Image.Resampling.NEAREST).save(out / "orcsdr_wave_small_anim_frame0_preview_10x.png")
    anim = {"name": "orcsdr_wave_small_anim", "kind": "gif", "file": "orcsdr_wave_small_anim.gif",
            "preview": "orcsdr_wave_small_anim_frame0_preview_10x.png", "project": "OrcSDR",
            "version": 2, "previous": "orcsdr_wave_small_anim_v1",
            "description": f"OrcSDR v2 layout (4x6 gradient wordmark at x={wm_x}, rows 0-5; 9-row spectrum) "
                           "with subtle spectrum jitter, 8 frames x 150 ms. Default OrcSDR project frame.",
            "generator": f"tools/make_wave_small.py --anim-only --wordmark-x {wm_x}",
            "params": {"wordmark": {"font": "4x6", "x": wm_x, "rows": "0-5", "stops": WORD_STOPS},
                       "spectrum": {"x": 17, "rows": "7-15", "bar_every": 2, "stops": BAR_STOPS, "jitter": 0.07},
                       "frames": 8, "frame_ms": 150}}
    (out / "orcsdr_wave_small_anim.json").write_text(json.dumps(anim, indent=2) + "\n", encoding="utf-8")
    print(f"wrote presets/orcsdr_wave_small_anim.* (wordmark x={wm_x})")


if __name__ == "__main__":
    main()
