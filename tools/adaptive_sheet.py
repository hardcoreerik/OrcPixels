"""Contact sheets for the adaptive layout work (docs/DESIGN.md):
  python tools/adaptive_sheet.py OUT_DIR   -> adaptive_scenes.png, adaptive_targets.png, adaptive_fallback.png"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from orcpixels import layout as L  # noqa: E402
from orcpixels import scenes as SCN  # noqa: E402


def font(n):
    for f in ("DejaVuSans.ttf", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(f, n)
        except OSError:
            pass
    return ImageFont.load_default()


def cell(img, scale):
    return L.led_preview(img, scale)


def sheet(rows, sizes, out, title, scale_for, pick):
    f1, f2 = font(15), font(12)
    cols = [max(s[0] * scale_for(s) for _ in [0]) for s in sizes]
    lw, pad = 190, 14
    rh = [max(s[1] * scale_for(s) for s in sizes) + 34 for _ in rows]
    W = lw + sum(cols) + pad * (len(sizes) + 1)
    H = 46 + sum(rh) + pad
    img = Image.new("RGB", (W, H), (14, 16, 20))
    d = ImageDraw.Draw(img)
    d.text((pad, 10), title, fill=(230, 235, 240), font=font(18))
    y = 46
    for (label, sub, fn), h in zip(rows, rh):
        d.text((pad, y + 4), label, fill=(235, 238, 242), font=f1)
        d.text((pad, y + 24), sub, fill=(150, 156, 170), font=f2)
        x = lw
        for s, cw in zip(sizes, cols):
            fr, tag = fn(*s)
            im = cell(pick(fr), scale_for(s))
            img.paste(im, (x, y))
            d.text((x, y + im.height + 3), f"{s[0]}x{s[1]} {L.classify(*s)} {L.tier(*s)} {tag}", fill=(150, 156, 170),
                   font=f2)
            x += cw + pad
        y += h
    img.save(out)
    return out


def sc_for(s):
    return max(2, min(8, 256 // max(s)))


def main(out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    def scene_row(name):
        def fn(w, h):
            fr, _ = SCN.frames(name, w, h)
            return fr, "adaptive"
        return (SCN.SCENES[name].title, name, fn)
    rows = [scene_row(n) for n in SCN.SCENES]
    pick = lambda fr: fr[len(fr) // 3]   # noqa: E731
    sheet(rows, SCN.TEST_SIZES, out / "adaptive_targets.png",
          "Adaptive scenes at Target sizes 16x16 / 64x32 / 256x16 / 96x24 (real renders, LED preview)",
          sc_for, pick)
    sheet(rows, [(64, 16), (64, 64), (128, 64), (128, 32), (128, 16), (32, 32), (32, 64), (96, 32)],
          out / "adaptive_scenes.png", "Adaptive scenes across shapes (real renders, LED preview)", sc_for, pick)
    # existing golden assets at the target sizes: rating + strategy
    from orcpixels import presets as PR
    from orcpixels import render as R
    from orcpixels import adaptive as AD

    def asset_row(name, strategy):
        def fn(w, h):
            fr, _, rating = AD.render_preset(name, w, h, strategy)
            return fr, rating
        return (f"{name}", f"strategy: {strategy}", fn)
    names = ["orcsdr_wave_small_anim", "p_orco_marquee"]
    rows2 = [asset_row(n, s) for n in names for s in ("adaptive", "contain", "crop")]
    sheet(rows2, SCN.TEST_SIZES + [(64, 16), (64, 64), (128, 32)], out / "adaptive_fallback.png",
          "Fixed (golden) presets at other sizes: rating Native / Scaled / Fallback per strategy", sc_for, pick)
    _ = (PR, R)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out")
