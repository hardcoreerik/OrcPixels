"""Generic auto-fit for panel sizes without a curated layout set.

OrcPixels has hand-made layouts for 64x16 and 64x64. Any other panel (32x8, 32x32,
64x32, 128x64, ...) renders at the curated size that fits it best (render_size) and the
result is scaled into the panel (fit): integer nearest-neighbour upscales stay crisp,
downscales average pixels (box filter), and the rest is letterboxed in black.
"""
from __future__ import annotations

from PIL import Image

CURATED = ((64, 16), (64, 64))


def _scale(src: tuple[int, int], dst: tuple[int, int]) -> float:
    return min(dst[0] / src[0], dst[1] / src[1])


def render_size(w: int, h: int) -> tuple[int, int]:
    """Curated layout size to render for a w x h panel.
    Preference: no downscaling (keeps 3x5 text readable), then the most panel area
    covered, then the closest aspect ratio."""
    if (w, h) in CURATED:
        return (w, h)
    best, key = CURATED[0], None
    for c in CURATED:
        s = _scale(c, (w, h))
        si = int(s) if s >= 1 else s
        cov = (c[0] * si) * (c[1] * si) / (w * h)
        k = (s >= 1, round(cov, 3), -abs((c[0] / c[1]) - (w / h)))
        if key is None or k > key:
            best, key = c, k
    return best


def fit(img: Image.Image, w: int, h: int) -> Image.Image:
    """img scaled into a w x h black canvas, centred (same size: returned as RGB copy)."""
    img = img.convert("RGB")
    if img.size == (w, h):
        return img.copy()
    s = _scale(img.size, (w, h))
    if s >= 1:
        s = int(s)
        nw, nh = img.width * s, img.height * s
        res = img.resize((nw, nh), Image.Resampling.NEAREST)
    else:
        nw, nh = max(1, round(img.width * s)), max(1, round(img.height * s))
        res = img.resize((nw, nh), Image.Resampling.BOX)
    out = Image.new("RGB", (w, h))
    out.paste(res, ((w - nw) // 2, (h - nh) // 2))
    return out
