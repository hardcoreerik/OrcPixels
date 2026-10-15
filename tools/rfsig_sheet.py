"""Preview sheet for tools/make_rfsig_presets.py: each RF / Signal preset (LED-dot render, 8x)
next to the matching crop of the concept boards. The boards are not in the repo: pass their
folder with --samples DIR (files a..e.png: uplink, sync grid, Midnight Synth sheet, hopping bars,
Style Direction sheet). Without it the sheet shows the presets only."""
from __future__ import annotations

import math
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
S = 1448 / 819          # board sheets were laid out on an 819 px grid
CROPS = {   # name -> (file, box in 819-grid coords or None = whole image)
    "p_sig_foxhunt": ("c", (14, 66, 805, 143)),
    "p_sig_jamstorm": ("c", (14, 172, 805, 255)),
    "p_sig_modwave": ("c", (14, 283, 805, 338)),
    "p_sig_beacon": ("e", (14, 82, 805, 162)),
    "p_sig_tuner": ("e", (14, 190, 805, 276)),
    "p_sig_satuplink": ("a", None),
    "p_sig_syncgrid": ("b", None),
    "p_sig_hophop": ("d", None),
    "p_sig_spectrum": ("e", (14, 422, 272, 602)),
    "p_sig_aprs": ("e", (546, 422, 805, 602)),
}
EXTRA = {"p_sig_jamstorm": ("e", (14, 306, 805, 386)), "p_sig_satuplink": ("e", (283, 422, 535, 602))}
BG, FG, DIMC = (14, 12, 26), (230, 220, 255), (150, 140, 190)


def _font():
    return ImageFont.truetype(str(ROOT / "orcpixels/fonts/unifont.otf"), 16)


def sample_crop(name, samples, box_w, box_h, which=CROPS):
    if not samples or name not in which:
        return None
    f, box = which[name]
    p = Path(samples) / f"{f}.png"
    if not p.exists():
        return None
    im = Image.open(p).convert("RGB")
    if box:
        im = im.crop(tuple(int(v * S) for v in box))
    k = min(box_w / im.width, box_h / im.height)
    return im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))), Image.Resampling.LANCZOS)


def _groups(results):
    out = {}
    for r in results:
        out.setdefault(r["name"], {})[r["_size"]] = r
    return out


def contact_sheet(results, path, scale=8, samples=None):
    """One row per concept: board crop (left, 512 px wide) | native preset at `scale`x as LED dots |
    the other-size companion at 4x."""
    from orcpixels.studio import led_frame
    samples = samples or os.environ.get("RFSIG_SAMPLES")
    font = _font()
    pad, lab, cw = 16, 24, 512
    rows = []
    for name, sizes in _groups(results).items():
        nat = [r for r in sizes.values() if r["_native"]][0]
        comp = next((r for r in sizes.values() if not r["_native"]), None)
        nimg = led_frame(nat["_frames"][len(nat["_frames"]) // 3], scale)
        cimg = led_frame(comp["_frames"][len(comp["_frames"]) // 3], 4) if comp else None
        crop = sample_crop(name, samples, cw, nimg.height if nimg.height > 200 else 9999)
        extra = sample_crop(name, samples, cw, 9999 if nimg.height <= 200 else 150, EXTRA)
        left_h = (crop.height if crop else 0) + ((extra.height + 8) if extra is not None else 0)
        if nimg.height > 200 and crop is not None and extra is not None and left_h > nimg.height + 160:
            extra = None
            left_h = crop.height
        h = max(left_h, nimg.height, (cimg.height + 22) if cimg else 0)
        rows.append((name, nat, comp, nimg, cimg, crop, extra, h))
    width = pad + cw + pad + 512 + pad + 256 + pad
    height = 52 + sum(lab + h + pad for *_, h in rows)
    sheet = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(sheet)
    d.text((pad, 14), "OrcPixels RF / Signal (rf_sig): concept board crop | native preset, 8x LED | other-size companion, 4x",
           fill=FG, font=font)
    y = 52
    for name, nat, comp, nimg, cimg, crop, extra, h in rows:
        m = nat["_loop"]
        d.text((pad, y), f"{name}  {nat['title']}   native {nat['_size'][0]}x{nat['_size'][1]}, {nat['frames']} fr x "
               f"{nat['frame_ms']} ms, {nat['bytes'] / 1024:.1f} KB, loop {'seamless' if m['ok'] else 'JUMP'}",
               fill=FG, font=font)
        yy = y + lab
        if crop is not None:
            sheet.paste(crop, (pad, yy))
            if extra is not None:
                sheet.paste(extra, (pad, yy + crop.height + 8))
        sheet.paste(nimg, (pad + cw + pad, yy))
        if cimg is not None:
            cx = pad + cw + pad + 512 + pad
            sheet.paste(cimg, (cx, yy))
            cm = comp["_loop"]
            d.text((cx, yy + cimg.height + 4), f"{comp['_size'][0]}x{comp['_size'][1]} {comp['bytes'] / 1024:.1f} KB "
                   f"{'seamless' if cm['ok'] else 'JUMP'}", fill=DIMC, font=font)
        y += lab + h + pad
    sheet.save(path)
    return path


def preview_gif(results, path, frames=48, ms=100, samples=None):
    """Animated LED-dot grid: every 64x16 build at 6x (two columns), then every 64x64 build at 3x."""
    from orcpixels.studio import led_frame
    font = _font()
    g = _groups(results)
    strips = [(n, s) for n, s in g.items() if (64, 16) in s]
    pad, lh = 10, 20
    col_w = 64 * 6 + pad
    # row A: every 64x16 (native or companion) at 4x, two columns; row B: every 64x64 at 3x, five columns
    s16 = [s[(64, 16)] for _, s in strips]
    s64 = [s[(64, 64)] for _, s in g.items() if (64, 64) in s]
    cols16, cols64 = 2, 5
    r16 = math.ceil(len(s16) / cols16)
    r64 = math.ceil(len(s64) / cols64)
    W = pad + max(cols16 * col_w, cols64 * (64 * 3 + pad))
    H = pad + r16 * (96 + lh + pad) + r64 * (192 + lh + pad) + pad
    base = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(base)
    pos = []
    for i, r in enumerate(s16):
        x, y = pad + (i % cols16) * col_w, pad + (i // cols16) * (96 + lh + pad)
        d.text((x, y), r["name"][6:] + ("" if r["_native"] else " (companion)"), fill=FG if r["_native"] else DIMC, font=font)
        pos.append((r, x, y + lh, 6))
    y0 = pad + r16 * (96 + lh + pad)
    for i, r in enumerate(s64):
        x, y = pad + (i % cols64) * (192 + pad), y0 + (i // cols64) * (192 + lh + pad)
        d.text((x, y), r["name"][6:] + ("" if r["_native"] else " (comp.)"), fill=FG if r["_native"] else DIMC, font=font)
        pos.append((r, x, y + lh, 3))
    out = []
    for k in range(frames):
        im = base.copy()
        for r, x, y, s in pos:
            fr = r["_frames"]
            fi = int(k * ms / r["frame_ms"]) % len(fr)
            im.paste(led_frame(fr[fi], s), (x, y))
        out.append(im)
    sample = Image.new("RGB", (W, H * 6))
    for i in range(6):
        sample.paste(out[i * len(out) // 6], (0, i * H))
    pal = sample.quantize(colors=255, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    q = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in out]
    q[0].save(path, save_all=True, append_images=q[1:], duration=ms, loop=0, optimize=False)
    return path
