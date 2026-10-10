"""Compact encoders for live frames (OrcSDR Live). The sign decodes PNG; a smaller PNG
means fewer BLE bytes. Frames are already 64x16 and rotated by the caller."""
from __future__ import annotations

import io

from PIL import Image


def png_rgb(img: Image.Image) -> bytes:
    """Reference: what SignClient.send_image sends (24-bit RGB PNG, Pillow defaults)."""
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def png_palette(img: Image.Image, max_colors: int = 16) -> bytes:
    """Indexed PNG with an exact palette of the colours actually used (live frames use a
    handful of brand colours; waterfall rows add gradient shades -> quantized to
    max_colors), smallest bit depth for the palette, zlib level 9, no metadata chunks."""
    rgb = img.convert("RGB")
    colors = rgb.getcolors(maxcolors=4096)
    if colors is not None and len(colors) <= max_colors:
        pal = [c for _, c in sorted(colors, key=lambda nc: -nc[0])]   # black first
        idx = {c: i for i, c in enumerate(pal)}
        p = Image.new("P", rgb.size)
        p.putpalette([v for c in pal for v in c])
        p.putdata([idx[px] for px in rgb.getdata()])
        n = len(pal)
    else:
        p = rgb.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT,
                         dither=Image.Dither.NONE)
        n = max_colors
    bits = 1 if n <= 2 else 2 if n <= 4 else 4 if n <= 16 else 8
    buf = io.BytesIO()
    p.save(buf, format="PNG", optimize=True, compress_level=9, bits=bits)
    return buf.getvalue()
