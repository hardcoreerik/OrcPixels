"""Regenerate derived LED assets from the user's source art.

    python tools/make_assets.py

Sources (user supplied):  assets/orcpixels_logo.png, assets/projects/orcsdr_banner.png
Outputs:
  assets/orc_16.png (+ _preview_20x)             OrcPixels app/brand icon
  assets/projects/orcsdr_16.png (+ preview)      OrcSDR project icon
  assets/projects/orcsdr_banner_64x16.png        icon + gradient "OrcSDR" wordmark
  assets/projects/orcsdr_wave_64x16.png          icon + mini spectrum from the banner
  (+ *_preview_10x.png for the 64x16 frames)

Straight downscaling of the logo turns the eyes/brows to mush at 16x16, so the
face is a hand-tuned pixel map (traced against a 16x16 BOX downscale of the
logo's face crop); colors are sampled from the source art.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orcpixels.render import gradient_text, spectrum_bars  # noqa: E402

A = ROOT / "assets"

# . black  G face  g shadow  L eye glint  W tusk
ORC = """
......GGGG......
.....GGGGGG.....
....GGGGGGGG....
G...GGGGGGGG...G
GG.GGGGGGGGGG.GG
.GG..GGGGGG..GG.
.gGG...GG...GGg.
..GGG.L..L.GGG..
...GGGGGGGGGG...
...GGGG..GGGG...
..WGGGGGGGGGGW..
..WWG.GGGG.GWW..
..WWGGW..WGGWW..
...WG......GW...
...gGGG..GGGg...
.....GGGGGG.....
"""


def pixmap(face, shadow, glint=(220, 255, 180), tusk=(230, 230, 230)) -> Image.Image:
    pal = {".": (0, 0, 0), "G": face, "g": shadow, "L": glint, "W": tusk}
    rows = ORC.strip().splitlines()
    assert len(rows) == 16 and all(len(r) == 16 for r in rows)
    img = Image.new("RGB", (16, 16))
    for y, r in enumerate(rows):
        for x, c in enumerate(r):
            img.putpixel((x, y), pal[c])
    return img


def save(img: Image.Image, path: Path, scale: int):
    img.save(path)
    img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST).save(
        path.with_name(path.stem + f"_preview_{scale}x.png"))
    print("wrote", path.relative_to(ROOT))


def sample(img: Image.Image, fx: float, fy: float):
    return img.getpixel((int(fx * img.width), int(fy * img.height)))


def spectrum_from_banner(banner: Image.Image, n: int) -> list[float]:
    """Heights (0..1) of the green waveform, sampled from the banner.

    The logo + wordmark box (x < 70% width, y < 43% height) is masked out so only
    the spectrum bars count; the reflection below 72% is ignored. A low percentile
    of per-pixel-column tops rejects the faint vertical grid lines.
    """
    g = banner.convert("RGB")
    w, h = g.size
    top, bottom = int(h * 0.30), int(h * 0.72)
    out = []
    for i in range(n):
        x0, x1 = int(i * w / n), int((i + 1) * w / n)
        tops = []
        for x in range(x0, x1):
            y_start = int(h * 0.43) if x < w * 0.70 else top
            t = bottom
            for y in range(y_start, bottom):
                r, gg, b = g.getpixel((x, y))
                if gg > 120 and gg > r + 30:
                    t = y
                    break
            tops.append(t)
        tops.sort()
        best = tops[len(tops) // 5]   # 20th percentile: ignores 1px grid lines, keeps peaks
        out.append((bottom - best) / (bottom - top))
    return out


def main():
    logo = Image.open(A / "orcpixels_logo.png").convert("RGB")
    face = sample(logo, 0.50, 0.40)          # face green of the OrcPixels logo
    orc = pixmap(face=(80, 255, 40) if face[1] < 200 else face, shadow=(20, 110, 10))
    save(orc, A / "orc_16.png", 20)

    banner = Image.open(A / "projects" / "orcsdr_banner.png").convert("RGB")
    bface = sample(banner, 262 / 1024, 140 / 576)       # lime face in the banner (~8cfc28)
    sdr = pixmap(face=bface, shadow=(40, 120, 10), glint=(230, 255, 140))
    save(sdr, A / "projects" / "orcsdr_16.png", 20)

    # 64x16 wordmark frame: icon + "OrcSDR" (6 x 8px Unifont = 48px) with the banner's
    # cyan -> green -> lime gradient.
    stops = [(210, 250, 255), (120, 250, 150), (210, 252, 56)]
    frame = Image.new("RGB", (64, 16))
    frame.paste(sdr, (0, 0))
    frame.paste(gradient_text("OrcSDR", 48, stops), (16, 0))
    save(frame, A / "projects" / "orcsdr_banner_64x16.png", 10)

    wave = Image.new("RGB", (64, 16))
    wave.paste(sdr, (0, 0))
    levels = spectrum_from_banner(banner, 47)
    peak = max(levels) or 1.0
    levels = [0.08 + 0.87 * v / peak for v in levels]      # normalize: peak fills the panel
    wave.paste(spectrum_bars(levels, 47, 16, stops), (17, 0))
    save(wave, A / "projects" / "orcsdr_wave_64x16.png", 10)


if __name__ == "__main__":
    main()
