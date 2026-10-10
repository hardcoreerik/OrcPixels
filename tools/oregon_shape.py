"""Oregon state outline for 64x16 art, rasterized from real geometry.

Source: assets/geo/oregon_ne10m.geojson, extracted from Natural Earth 1:10m
admin_1_states_provinces (public domain). Equirectangular projection with
cos(44.2 deg) x-scaling (looks right at this latitude), 16x supersampled polygon
fill, coverage threshold, then a 1-px 8-connected border for the outline.

    python tools/oregon_shape.py      # writes assets/oregon_*.png + 10x previews
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
GEO = ROOT / "assets" / "geo" / "oregon_ne10m.geojson"
SS = 16
LAT_MID = 44.2

CITIES = {"Portland": (-122.68, 45.52), "Salem": (-123.03, 44.94), "Eugene": (-123.09, 44.05),
          "Bend": (-121.31, 44.06), "Mt Hood": (-121.70, 45.37), "Medford": (-122.87, 42.33),
          "Astoria": (-123.83, 46.19), "Pendleton": (-118.79, 45.67)}


@lru_cache(None)
def rings() -> list[list[tuple[float, float]]]:
    g = json.loads(GEO.read_text(encoding="utf-8"))["geometry"]
    polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    return [[tuple(p) for p in poly[0]] for poly in polys]


@lru_cache(None)
def bbox():
    pts = [p for r in rings() for p in r]
    return (min(p[0] for p in pts), min(p[1] for p in pts), max(p[0] for p in pts), max(p[1] for p in pts))


def _geom(h: int):
    lon0, lat0, lon1, lat1 = bbox()
    s = h / (lat1 - lat0)
    k = s * math.cos(math.radians(LAT_MID))
    w = max(1, round((lon1 - lon0) * k))
    return lon0, lat1, s, k, w


def project(lon: float, lat: float, h: int = 16) -> tuple[int, int]:
    lon0, lat1, s, k, w = _geom(h)
    return (min(w - 1, max(0, int((lon - lon0) * k))), min(h - 1, max(0, int((lat1 - lat) * s))))


@lru_cache(None)
def mask(h: int = 16, thresh: float = 0.42) -> Image.Image:
    """Filled Oregon, 'L' 0/255, exactly h rows tall."""
    lon0, lat1, s, k, w = _geom(h)
    big = Image.new("L", (w * SS, h * SS), 0)
    d = ImageDraw.Draw(big)
    for r in rings():
        d.polygon([((lon - lon0) * k * SS, (lat1 - lat) * s * SS) for lon, lat in r], fill=255)
    small = big.resize((w, h), Image.Resampling.BOX)
    m = small.point(lambda v: 255 if v >= thresh * 255 else 0)
    # drop specks: filled pixels with fewer than 2 filled 4-neighbours (river slivers)
    px = m.load()
    changed = True
    while changed:
        changed = False
        for y in range(h):
            for x in range(w):
                if px[x, y] and sum(1 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                                    if 0 <= x + dx < w and 0 <= y + dy < h and px[x + dx, y + dy]) < 2:
                    px[x, y] = 0
                    changed = True
    return m


@lru_cache(None)
def outline(h: int = 16) -> Image.Image:
    """1-px border of mask(h): filled pixels with an empty 4-neighbour (or the image edge)."""
    m = mask(h)
    w = m.width
    src = m.load()
    out = Image.new("L", m.size, 0)
    px = out.load()
    for y in range(h):
        for x in range(w):
            if not src[x, y]:
                continue
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if not (0 <= nx < w and 0 <= ny < h) or not src[nx, ny]:
                    px[x, y] = 255
                    break
    return out


def colored(m: Image.Image, color) -> Image.Image:
    img = Image.new("RGBA", m.size, (0, 0, 0, 0))
    img.paste(Image.new("RGBA", m.size, tuple(color) + (255,)), (0, 0), m)
    return img


def main():
    out = ROOT / "assets"
    for h in (16, 12, 9):
        for kind, m in (("outline", outline(h)), ("fill", mask(h))):
            img = colored(m, (60, 220, 90))
            p = out / f"oregon_{kind}_{h}.png"
            img.save(p)
            prev = Image.new("RGB", img.size, (0, 0, 0))
            prev.paste(img, (0, 0), img)
            prev.resize((img.width * 20, img.height * 20), Image.Resampling.NEAREST).save(
                out / f"oregon_{kind}_{h}_preview_20x.png")
            print(p.name, img.size)


if __name__ == "__main__":
    main()
