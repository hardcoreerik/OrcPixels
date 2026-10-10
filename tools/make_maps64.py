"""Land masks for the 64x64 Live Data screens (ISS track, FT8 maps).

Rasterized from Natural Earth 1:110m land (public domain), equirectangular, 16x
supersampled, coverage >= 0.35:
  assets/world_land_64x32.png   lon -180..180, lat 90..-90
  assets/na_land_64x40.png      lon -128..-62, lat 56..20   (NA_BOX64 in live64.py)

    python tools/make_maps64.py path/to/ne_110m_land.geojson
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SS = 16


def build(src, w, h, box, thresh=0.35):
    lo0, lo1, la0, la1 = box
    d = json.loads(Path(src).read_text(encoding="utf-8"))
    big = Image.new("L", (w * SS, h * SS), 0)
    dr = ImageDraw.Draw(big)
    sx, sy = w * SS / (lo1 - lo0), h * SS / (la1 - la0)
    for f in d["features"]:
        g = f["geometry"]
        if not g:
            continue
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            ring = poly[0]
            if len(ring) >= 3:
                dr.polygon([((lon - lo0) * sx, (la1 - lat) * sy) for lon, lat in ring], fill=255)
    small = big.resize((w, h), Image.Resampling.BOX)
    return small.point(lambda v: 255 if v >= thresh * 255 else 0)


if __name__ == "__main__":
    src = sys.argv[1]
    build(src, 64, 32, (-180, 180, -90, 90)).save(ROOT / "assets" / "world_land_64x32.png")
    build(src, 64, 40, (-128, -62, 20, 56)).save(ROOT / "assets" / "na_land_64x40.png")
    print("ok")
