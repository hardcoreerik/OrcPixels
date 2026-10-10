"""World land mask for the Live Data ISS screen (assets/world_land_32x16.png).

Rasterized from Natural Earth 1:10m admin-1 states/provinces (public domain; the union
of all provinces = land), equirectangular lon -180..180 x lat 90..-90, 16x supersampled
coverage >= 0.3. The 40 MB GeoJSON is not in the repo:

    python tools/make_world_map.py path/to/ne_10m_admin_1_states_provinces.geojson
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SS = 16


def build(src: str, w: int = 32, h: int = 16, thresh: float = 0.3) -> Image.Image:
    d = json.loads(Path(src).read_text(encoding="utf-8"))
    big = Image.new("L", (w * SS, h * SS), 0)
    dr = ImageDraw.Draw(big)
    sx, sy = w * SS / 360.0, h * SS / 180.0
    for f in d["features"]:
        g = f["geometry"]
        if not g:
            continue
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        for poly in polys:
            ring = poly[0]
            if len(ring) >= 3:
                dr.polygon([((lon + 180) * sx, (90 - lat) * sy) for lon, lat in ring], fill=255)
    small = big.resize((w, h), Image.Resampling.BOX)
    return small.point(lambda v: 255 if v >= thresh * 255 else 0)


if __name__ == "__main__":
    m = build(sys.argv[1])
    out = ROOT / "assets" / "world_land_32x16.png"
    m.save(out)
    for y in range(m.height):
        print("".join("#" if m.getpixel((x, y)) else "." for x in range(m.width)))
    print("wrote", out)
